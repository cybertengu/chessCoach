"""A local SQLite database of your own mistakes and how your retries went.

One row per flagged move you made (``mistakes``), the game it came from
(``games``), and every retry event: each attempt and each time you revealed
the answer (``retries``). The ``mistake_stats`` view sums the retries up per
mistake. Later features (pattern tags, reports, the practice deck) query this.

The schema is versioned with ``PRAGMA user_version``; to change it, append a
script to ``MIGRATIONS``. Never edit a script that has already shipped.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import chess

from .analysis import Evaluation, GameAnalysis, MoveAnalysis
from .classify import MoveJudgement, flagged_moves
from .retry import AttemptResult

ENV_VAR = "CHESSCOACH_DB"

# MIGRATIONS[i] upgrades the schema from version i to version i + 1.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE games (
        id INTEGER PRIMARY KEY,
        key TEXT NOT NULL UNIQUE,          -- see game_key()
        link TEXT,                         -- PGN Link/Site URL, if any
        white TEXT,
        black TEXT,
        result TEXT,
        date TEXT,                         -- ISO yyyy-mm-dd, NULL if unknown
        time_control TEXT,                 -- raw PGN TimeControl, e.g. "600+5"
        base_seconds INTEGER,              -- parsed from time_control, NULL if not "base+inc"
        increment_seconds INTEGER,
        my_color TEXT NOT NULL CHECK (my_color IN ('white', 'black')),
        nodes INTEGER,                     -- engine node budget the verdicts came from
        added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    ) STRICT;

    CREATE TABLE mistakes (
        id INTEGER PRIMARY KEY,
        game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        ply INTEGER NOT NULL,
        move_number INTEGER NOT NULL,
        color TEXT NOT NULL CHECK (color IN ('white', 'black')),
        fen TEXT NOT NULL,                 -- position before the move
        move_uci TEXT NOT NULL,
        move_san TEXT NOT NULL,
        best_uci TEXT,
        best_san TEXT,
        best_line_san TEXT,                -- space-separated engine line
        eval_before_cp INTEGER NOT NULL,   -- White's point of view, mates folded in
        eval_before_mate INTEGER,
        eval_after_cp INTEGER NOT NULL,
        eval_after_mate INTEGER,
        win_before REAL NOT NULL,          -- mover's win %, 0-100
        win_after REAL NOT NULL,
        win_loss REAL NOT NULL,
        judgement TEXT NOT NULL CHECK (judgement IN ('inaccuracy', 'mistake', 'blunder')),
        clock_seconds REAL,                -- mover's clock after the move, if recorded
        UNIQUE (game_id, ply)
    ) STRICT;

    CREATE TABLE retries (
        id INTEGER PRIMARY KEY,
        mistake_id INTEGER NOT NULL REFERENCES mistakes(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('attempt', 'reveal')),
        move_uci TEXT,                     -- attempts only
        move_san TEXT,
        judgement TEXT,
        win_after REAL,
        solved INTEGER CHECK (solved IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    ) STRICT;

    CREATE INDEX retries_by_mistake ON retries (mistake_id);

    CREATE VIEW mistake_stats AS
    SELECT
        m.id AS mistake_id,
        COUNT(r.id) FILTER (WHERE r.kind = 'attempt') AS attempts,
        COALESCE(MAX(r.solved), 0) AS solved,
        COALESCE(MAX(r.kind = 'reveal'), 0) AS revealed
    FROM mistakes m LEFT JOIN retries r ON r.mistake_id = m.id
    GROUP BY m.id;
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


class DatabaseError(RuntimeError):
    """The mistake database could not be opened or written."""


def default_db_path() -> Path:
    """``$CHESSCOACH_DB``, else ``$XDG_DATA_HOME/chesscoach/mistakes.sqlite3``."""
    if os.environ.get(ENV_VAR):
        return Path(os.environ[ENV_VAR])
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "chesscoach" / "mistakes.sqlite3"


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise DatabaseError(
            f"The mistake database is from a newer chessCoach (schema {version}, "
            f"this version knows {SCHEMA_VERSION})."
        )
    for target in range(version + 1, SCHEMA_VERSION + 1):
        # executescript() commits first, so run the script and the version
        # bump inside one explicit transaction.
        conn.executescript(
            f"BEGIN; {MIGRATIONS[target - 1]}; PRAGMA user_version = {target}; COMMIT;"
        )


def game_key(headers: dict[str, str], moves: list[str], start_fen: str) -> str:
    """What makes two imports "the same game".

    The game's URL when the PGN has one (Chess.com's ``Link``, Lichess's
    ``Site``); otherwise a hash of the moves, players and date.
    """
    link = game_link(headers)
    if link:
        return f"link:{link}"
    data = {
        "fen": start_fen,
        "moves": moves,
        **{k: headers.get(k, "") for k in ("White", "Black", "Date", "UTCDate", "UTCTime")},
    }
    return "moves:" + hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def game_link(headers: dict[str, str]) -> str | None:
    for name in ("Link", "Site"):
        value = headers.get(name, "").strip()
        if value.startswith(("http://", "https://")):
            return value
    return None


def parse_date(headers: dict[str, str]) -> str | None:
    """PGN ``Date`` (or ``UTCDate``) as ISO ``yyyy-mm-dd``; None if incomplete."""
    for name in ("Date", "UTCDate"):
        m = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})", headers.get(name, "").strip())
        if m:
            return "-".join(m.groups())
    return None


def parse_time_control(value: str | None) -> tuple[int | None, int | None]:
    """``"600+5"`` → (600, 5); ``"300"`` → (300, 0); anything else → (None, None)."""
    m = re.fullmatch(r"(\d+)(?:\+(\d+))?", (value or "").strip())
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2) or 0)


@dataclass(frozen=True)
class RecordedGame:
    game_id: int
    mistake_ids: dict[int, int]  # ply -> mistakes.id, for every stored mistake of the game
    added: int  # how many of those were new


class MistakeDB:
    """The mistake database at ``path`` (default: :func:`default_db_path`).

    Every method opens its own short-lived connection, so one instance can be
    used from any thread.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path) if path else default_db_path()

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """A migrated connection; commits on success, rolls back on error."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=10)
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseError(f"Could not open {self.path}: {exc}") from exc
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            _migrate(conn)
            with conn:
                yield conn
        except sqlite3.Error as exc:
            raise DatabaseError(f"Mistake database error: {exc}") from exc
        finally:
            conn.close()

    def record_game(
        self, analysis: GameAnalysis, judgements: list[MoveJudgement], my_color: chess.Color
    ) -> RecordedGame:
        """Store your flagged moves from ``analysis``. Safe to call again for the same game.

        Mistakes already stored are kept as they are (with their retry
        history). If you now say you played the other side, the mistakes
        stored for the old side are removed: they were not yours.
        """
        headers = analysis.headers
        color = "white" if my_color == chess.WHITE else "black"
        start_fen = analysis.moves[0].fen_before if analysis.moves else chess.STARTING_FEN
        key = game_key(headers, [m.uci for m in analysis.moves], start_fen)
        base, inc = parse_time_control(headers.get("TimeControl"))

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO games (key, link, white, black, result, date, time_control,
                                   base_seconds, increment_seconds, my_color, nodes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (key) DO UPDATE SET my_color = excluded.my_color
                """,
                (
                    key, game_link(headers), headers.get("White"), headers.get("Black"),
                    headers.get("Result"), parse_date(headers), headers.get("TimeControl"),
                    base, inc, color, analysis.nodes,
                ),
            )
            game_id = conn.execute("SELECT id FROM games WHERE key = ?", (key,)).fetchone()[0]
            conn.execute("DELETE FROM mistakes WHERE game_id = ? AND color != ?", (game_id, color))

            added = 0
            for j in flagged_moves(judgements, my_color):
                m = j.move
                cur = conn.execute(
                    """
                    INSERT INTO mistakes (game_id, ply, move_number, color, fen, move_uci,
                        move_san, best_uci, best_san, best_line_san, eval_before_cp,
                        eval_before_mate, eval_after_cp, eval_after_mate, win_before,
                        win_after, win_loss, judgement, clock_seconds)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (game_id, ply) DO NOTHING
                    """,
                    (
                        game_id, m.ply, m.move_number, m.color, m.fen_before, m.uci, m.san,
                        m.best_move_uci, m.best_move_san, " ".join(m.best_line_san),
                        m.eval_before.cp, m.eval_before.mate, m.eval_after.cp,
                        m.eval_after.mate, j.win_before, j.win_after, j.win_loss,
                        j.judgement.value, m.clock_seconds,
                    ),
                )
                added += cur.rowcount
            ids = dict(conn.execute("SELECT ply, id FROM mistakes WHERE game_id = ?", (game_id,)))
        return RecordedGame(game_id, ids, added)

    def record_attempt(self, mistake_id: int, result: AttemptResult) -> None:
        """Log one judged retry attempt."""
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO retries (mistake_id, kind, move_uci, move_san, judgement,
                                     win_after, solved)
                VALUES (?, 'attempt', ?, ?, ?, ?, ?)
                """,
                (
                    mistake_id, result.move_uci, result.san, result.judgement.value,
                    result.win_after, int(result.solved),
                ),
            )

    def record_reveal(self, mistake_id: int) -> None:
        """Log that the answer to a mistake was shown."""
        with self.connect() as conn:
            conn.execute("INSERT INTO retries (mistake_id, kind) VALUES (?, 'reveal')", (mistake_id,))


def mistake_from_row(row: sqlite3.Row) -> MoveAnalysis:
    """Rebuild the :class:`MoveAnalysis` of a stored mistake, e.g. to retry it."""
    return MoveAnalysis(
        ply=row["ply"],
        move_number=row["move_number"],
        color=row["color"],
        san=row["move_san"],
        uci=row["move_uci"],
        fen_before=row["fen"],
        eval_before=Evaluation(row["eval_before_cp"], row["eval_before_mate"]),
        eval_after=Evaluation(row["eval_after_cp"], row["eval_after_mate"]),
        best_move_uci=row["best_uci"],
        best_move_san=row["best_san"],
        best_line_san=(row["best_line_san"] or "").split(),
        clock_seconds=row["clock_seconds"],
    )
