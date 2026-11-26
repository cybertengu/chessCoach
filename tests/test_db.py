import sqlite3

import chess
import pytest

import chesscoach.db as db_mod
from chesscoach.analysis import Evaluation, GameAnalysis, MoveAnalysis
from chesscoach.classify import Judgement, judge_game
from chesscoach.db import (
    DatabaseError,
    MistakeDB,
    default_db_path,
    game_key,
    mistake_from_row,
    parse_date,
    parse_time_control,
)
from chesscoach.retry import AttemptResult

HEADERS = {
    "White": "UsagiTengu",
    "Black": "Opponent",
    "Result": "0-1",
    "Date": "2026.11.20",
    "TimeControl": "600+5",
    "Link": "https://www.chess.com/game/live/123",
}


def _analysis(headers=HEADERS, nodes=200_000) -> GameAnalysis:
    """1. e4 e5 2. Qh5 Nc6 3. Qxe5+?? with hand-made evaluations.

    White's 2. Qh5 is an inaccuracy and 3. Qxe5+ hangs the queen; Black plays fine.
    """
    sans = ["e4", "e5", "Qh5", "Nc6", "Qxe5+"]
    evals = [30, 30, 30, -30, -30, -900]  # before each move, then after the last
    best = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"]
    clocks = [598.0, 599.0, 590.0, 595.0, 41.5]
    board = chess.Board()
    moves = []
    for i, san in enumerate(sans):
        move = board.parse_san(san)
        best_move = chess.Move.from_uci(best[i])
        moves.append(
            MoveAnalysis(
                ply=i + 1,
                move_number=board.fullmove_number,
                color="white" if board.turn == chess.WHITE else "black",
                san=san,
                uci=move.uci(),
                fen_before=board.fen(),
                eval_before=Evaluation(evals[i]),
                eval_after=Evaluation(evals[i + 1]),
                best_move_uci=best_move.uci(),
                best_move_san=board.san(best_move),
                best_line_san=[board.san(best_move), "Nf6"],
                clock_seconds=clocks[i],
            )
        )
        board.push(move)
    return GameAnalysis(headers=dict(headers), moves=moves, nodes=nodes)


def _record(db, color=chess.WHITE, analysis=None):
    analysis = analysis or _analysis()
    return db.record_game(analysis, judge_game(analysis), color)


def _attempt(san="Nf3", uci="g1f3", judgement=Judgement.GOOD) -> AttemptResult:
    return AttemptResult(
        move_uci=uci, san=san, judgement=judgement, win_before=52.0, win_after=51.0,
        original_win_after=10.0, evaluation=Evaluation(20), reply_line_san=[],
    )


def test_db_path_follows_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CHESSCOACH_DB", str(tmp_path / "x.db"))
    assert default_db_path() == tmp_path / "x.db"
    monkeypatch.delenv("CHESSCOACH_DB")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_db_path() == tmp_path / "chesscoach" / "mistakes.sqlite3"


def test_tests_use_a_private_database(tmp_path):
    assert MistakeDB().path == tmp_path / "mistakes.sqlite3"


def test_header_parsing():
    assert parse_time_control("600+5") == (600, 5)
    assert parse_time_control("180") == (180, 0)
    assert parse_time_control("1/86400") == (None, None)
    assert parse_time_control("-") == (None, None)
    assert parse_time_control(None) == (None, None)
    assert parse_date({"Date": "2026.11.20"}) == "2026-11-20"
    assert parse_date({"Date": "????.??.??", "UTCDate": "2026.11.21"}) == "2026-11-21"
    assert parse_date({}) is None


def test_game_key_prefers_the_link():
    moves = ["e2e4"]
    assert game_key(HEADERS, moves, chess.STARTING_FEN) == "link:https://www.chess.com/game/live/123"
    lichess = {"Site": "https://lichess.org/abc"}
    assert game_key(lichess, moves, chess.STARTING_FEN) == "link:https://lichess.org/abc"
    # No URL ("Site" is just a name): fall back to a hash of moves, players and date.
    plain = {"Site": "Chess.com", "White": "a", "Black": "b"}
    key = game_key(plain, moves, chess.STARTING_FEN)
    assert key.startswith("moves:")
    assert key == game_key(dict(plain), list(moves), chess.STARTING_FEN)
    assert key != game_key(plain, ["d2d4"], chess.STARTING_FEN)
    assert key != game_key({**plain, "White": "c"}, moves, chess.STARTING_FEN)


def test_stores_only_my_mistakes_with_all_details():
    db = MistakeDB()
    recorded = _record(db)
    assert recorded.added == 2
    assert sorted(recorded.mistake_ids) == [3, 5]

    with db.connect() as conn:
        game = conn.execute("SELECT * FROM games").fetchone()
        rows = conn.execute("SELECT * FROM mistakes ORDER BY ply").fetchall()
    assert game["link"] == "https://www.chess.com/game/live/123"
    assert game["date"] == "2026-11-20"
    assert game["time_control"] == "600+5"
    assert (game["base_seconds"], game["increment_seconds"]) == (600, 5)
    assert game["my_color"] == "white"
    assert game["nodes"] == 200_000

    assert [r["color"] for r in rows] == ["white", "white"]
    blunder = rows[1]
    assert blunder["move_san"] == "Qxe5+"
    assert blunder["move_uci"] == "h5e5"
    assert blunder["best_san"] == "Bc4"
    assert blunder["best_line_san"] == "Bc4 Nf6"
    assert blunder["judgement"] == "blunder"
    assert blunder["win_loss"] == pytest.approx(blunder["win_before"] - blunder["win_after"])
    assert blunder["win_loss"] > 30
    assert blunder["clock_seconds"] == 41.5
    assert chess.Board(blunder["fen"]).san(chess.Move.from_uci("h5e5")) == "Qxe5+"


def test_reopening_a_game_adds_nothing():
    db = MistakeDB()
    first = _record(db)
    db.record_attempt(first.mistake_ids[5], _attempt())
    again = _record(db)
    assert again.added == 0
    assert again == db_mod.RecordedGame(first.game_id, first.mistake_ids, 0)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM mistakes").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM retries").fetchone()[0] == 1


def test_same_moves_from_another_game_are_kept_apart():
    db = MistakeDB()
    _record(db)
    other = _analysis({**HEADERS, "Link": "https://www.chess.com/game/live/456"})
    assert _record(db, analysis=other).added == 2
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM mistakes").fetchone()[0] == 4


def test_switching_sides_replaces_the_other_sides_mistakes():
    db = MistakeDB()
    _record(db, chess.WHITE)
    as_black = _record(db, chess.BLACK)
    assert as_black.mistake_ids == {}  # Black made no mistakes in this game
    with db.connect() as conn:
        assert conn.execute("SELECT my_color FROM games").fetchone()[0] == "black"
        assert conn.execute("SELECT COUNT(*) FROM mistakes").fetchone()[0] == 0


def test_retry_outcomes_are_summed_per_mistake():
    db = MistakeDB()
    ids = _record(db).mistake_ids
    db.record_attempt(ids[5], _attempt("Qe2", "h5e2", Judgement.INACCURACY))
    db.record_reveal(ids[5])
    db.record_attempt(ids[5], _attempt("Bc4", "f1c4", Judgement.BEST))
    db.record_attempt(ids[3], _attempt("Qxf7+", "h5f7", Judgement.BLUNDER))

    with db.connect() as conn:
        stats = {
            r["mistake_id"]: (r["attempts"], r["solved"], r["revealed"])
            for r in conn.execute("SELECT * FROM mistake_stats")
        }
        first = conn.execute("SELECT * FROM retries ORDER BY id").fetchone()
    assert stats == {ids[5]: (2, 1, 1), ids[3]: (1, 0, 0)}
    assert (first["kind"], first["move_san"], first["judgement"], first["solved"]) == (
        "attempt", "Qe2", "inaccuracy", 0,
    )
    assert first["created_at"].endswith("Z")


def test_mistake_round_trips_for_retrying():
    db = MistakeDB()
    ids = _record(db).mistake_ids
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM mistakes WHERE id = ?", (ids[5],)).fetchone()
    assert mistake_from_row(row) == _analysis().moves[4]


def test_schema_is_versioned(tmp_path):
    db = MistakeDB(tmp_path / "v.db")
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db_mod.SCHEMA_VERSION
    # A database from a newer chessCoach is refused rather than misread.
    with sqlite3.connect(db.path) as raw:
        raw.execute(f"PRAGMA user_version = {db_mod.SCHEMA_VERSION + 1}")
    with pytest.raises(DatabaseError, match="newer"):
        with db.connect():
            pass


def test_later_migrations_run_on_an_existing_database(tmp_path, monkeypatch):
    db = MistakeDB(tmp_path / "m.db")
    _record(db)
    extra = "ALTER TABLE mistakes ADD COLUMN tags TEXT"
    monkeypatch.setattr(db_mod, "MIGRATIONS", [*db_mod.MIGRATIONS, extra])
    monkeypatch.setattr(db_mod, "SCHEMA_VERSION", len(db_mod.MIGRATIONS))
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db_mod.SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(tags) FROM mistakes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM mistakes").fetchone()[0] == 2


def test_unwritable_location_raises_database_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    with pytest.raises(DatabaseError):
        _record(MistakeDB(blocker / "sub" / "x.db"))


def test_stores_the_opponents_best_reply_line():
    db = MistakeDB()
    ids = _record(db).mistake_ids
    with db.connect() as conn:
        rows = dict(conn.execute("SELECT ply, refutation_line_san FROM mistakes"))
    # 2. Qh5: the next position's engine line. 3. Qxe5+ ended the game: nothing searched.
    assert rows == {3: "Nc6 Nf6", 5: None}
    assert set(ids) == {3, 5}


def test_version_1_database_upgrades_with_its_mistakes(tmp_path):
    path = tmp_path / "v1.db"
    with sqlite3.connect(path) as raw:
        raw.executescript(db_mod.MIGRATIONS[0])
        raw.execute("PRAGMA user_version = 1")
        raw.execute(
            "INSERT INTO games (key, my_color) VALUES ('k', 'white')"
        )
        raw.execute(
            """INSERT INTO mistakes (game_id, ply, move_number, color, fen, move_uci, move_san,
                   eval_before_cp, eval_after_cp, win_before, win_after, win_loss, judgement)
               VALUES (1, 5, 3, 'white', ?, 'h5e5', 'Qxe5+', -30, -900, 46, 4, 42, 'blunder')""",
            (chess.STARTING_FEN,),
        )
    db = MistakeDB(path)
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db_mod.SCHEMA_VERSION
        row = conn.execute("SELECT refutation_line_san, tags_version FROM mistakes").fetchone()
        assert tuple(row) == (None, None)
        conn.execute("INSERT INTO mistake_tags VALUES (1, 'hung_piece')")
        conn.execute("DELETE FROM mistakes")
        assert conn.execute("SELECT COUNT(*) FROM mistake_tags").fetchone()[0] == 0
