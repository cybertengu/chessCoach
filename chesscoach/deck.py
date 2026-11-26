"""The practice deck: your own mistakes as puzzles that come back on a schedule.

Scheduling uses Leitner boxes. A card in box ``n`` comes back after
``INTERVALS[n]`` days. How each review is graded:

* pass: your first move was best or good and you used no hint. The card
  moves up a box; a new card goes straight to box 2.
* hard: you solved it after a wrong try or a hint. The card stays in its
  box (a new card goes to box 1).
* fail: you revealed the answer before solving it, or left the card unsolved
  after a wrong try or a hint. The card drops back to box 1.

Leaving a card before doing anything is a skip: nothing is recorded and it
stays due.

Which mistakes become cards: your mistakes and blunders (not inaccuracies)
with a known best move, except those made when you were already losing. A
position that occurs more than once is one card. Cards enter the deck
:data:`NEW_PER_DAY` at a time, blunders first, then the newest games.
:class:`Deck` keeps the schedule in the mistake database (``deck`` and
``reviews`` tables); it never shows or stores answers beyond what's there.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass, field
from enum import Enum

import chess

from .analysis import MoveAnalysis
from .classify import Judgement
from .db import _TAG_VALUES, MistakeDB, mistake_from_row
from .tags import Tag, TagGroup, sort_tags

# Box -> days until the card is due again. Box 0 is a new card.
INTERVALS: dict[int, int] = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30, 6: 60, 7: 120}
MAX_BOX = max(INTERVALS)
NEW_BOX = 0
# A new card that passes on its first review skips box 1.
FIRST_PASS_BOX = 2
# How many cards are introduced to the deck per day.
NEW_PER_DAY = 10
# A card missed this many times is a "trouble card" in the report.
TROUBLE_LAPSES = 3
# How many trouble cards the report lists.
TROUBLE_SHOWN = 10


class Grade(Enum):
    PASS = "pass"
    HARD = "hard"
    FAIL = "fail"


@dataclass(frozen=True)
class Scheduled:
    box: int
    due: dt.date


def schedule(box: int, grade: Grade, today: dt.date) -> Scheduled:
    """Where a card in ``box`` goes after a review graded ``grade`` on ``today``."""
    if not NEW_BOX <= box <= MAX_BOX:
        raise ValueError(f"no box {box}")
    if grade is Grade.FAIL:
        new_box = 1
    elif grade is Grade.HARD:
        new_box = max(box, 1)
    elif box == NEW_BOX:
        new_box = FIRST_PASS_BOX
    else:
        new_box = min(box + 1, MAX_BOX)
    return Scheduled(new_box, today + dt.timedelta(days=INTERVALS[new_box]))


class Review:
    """Grades one review of one card from what happens while you work on it.

    The grade is fixed by the first thing that settles it: a solved attempt
    (pass or hard) or revealing the answer (fail). Anything afterwards, like
    playing the revealed move, does not change it.
    """

    def __init__(self) -> None:
        self.wrong_attempts = 0
        self.used_hint = False
        self.grade: Grade | None = None

    @property
    def decided(self) -> bool:
        return self.grade is not None

    def attempt(self, solved: bool) -> None:
        if self.decided:
            return
        if not solved:
            self.wrong_attempts += 1
        elif self.wrong_attempts or self.used_hint:
            self.grade = Grade.HARD
        else:
            self.grade = Grade.PASS

    def hint(self) -> None:
        if not self.decided:
            self.used_hint = True

    def reveal(self) -> None:
        if not self.decided:
            self.grade = Grade.FAIL

    def grade_on_leave(self) -> Grade | None:
        """The grade when you move on from the card; None means it was skipped."""
        if self.grade is not None:
            return self.grade
        if self.wrong_attempts or self.used_hint:
            return Grade.FAIL
        return None


@dataclass(frozen=True)
class Card:
    """One puzzle of the deck: a stored mistake and where it is in the schedule."""

    mistake_id: int
    game_id: int
    box: int  # NEW_BOX for a card not reviewed yet
    due: dt.date | None  # None for a new card
    lapses: int
    mistake: MoveAnalysis
    judgement: Judgement
    win_before: float  # your win % with best play
    win_after: float  # ... after the move you played
    tags: list[Tag]
    white: str | None
    black: str | None
    game_date: str | None  # ISO yyyy-mm-dd
    link: str | None
    has_pgn: bool

    @property
    def is_new(self) -> bool:
        return self.box == NEW_BOX

    @property
    def color(self) -> chess.Color:
        return chess.WHITE if self.mistake.color == "white" else chess.BLACK

    @property
    def opponent(self) -> str:
        return (self.black if self.color == chess.WHITE else self.white) or "?"


@dataclass(frozen=True)
class DeckSummary:
    due: int  # cards due today, overdue ones included
    new_today: int  # new cards today's session still introduces
    new_waiting: int  # eligible cards not in the deck yet
    boxes: dict[int, int]  # box -> cards in it, for every box
    upcoming: list[tuple[dt.date, int]]  # cards due on each of the next days
    recent: dict[Grade, int] = field(default_factory=dict)  # reviews in the last 30 days

    @property
    def in_deck(self) -> int:
        return sum(self.boxes.values())

    @property
    def session_size(self) -> int:
        return self.due + self.new_today


@dataclass(frozen=True)
class PatternPractice:
    """How reviews of the cards with one pattern tag went."""

    tag: Tag
    cards: int  # cards in the deck with this tag
    reviews: int
    passed: int  # found a good move on the first try

    @property
    def pass_rate(self) -> float:
        return 100.0 * self.passed / self.reviews if self.reviews else 0.0


@dataclass(frozen=True)
class DeckReport:
    summary: DeckSummary
    patterns: list[PatternPractice]  # most reviewed first
    trouble: list[Card]  # missed TROUBLE_LAPSES times or more, most missed first

    @property
    def empty(self) -> bool:
        return not self.summary.in_deck and not self.summary.new_waiting


# Mistakes that may become cards (``m`` is the mistakes row).
_ELIGIBLE = f"""
    m.judgement IN ('{Judgement.MISTAKE.value}', '{Judgement.BLUNDER.value}')
    AND m.best_uci IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM mistake_tags t
        WHERE t.mistake_id = m.id AND t.tag = '{Tag.ALREADY_LOSING.value}'
    )
"""

# Eligible mistakes not in the deck yet, one per position, in the order they
# are introduced: blunders first, then the newest games, then by move.
_NEW_CARDS = f"""
    WITH candidates AS (
        SELECT m.id, m.judgement, m.game_id, m.ply, g.date,
               ROW_NUMBER() OVER (
                   PARTITION BY m.fen ORDER BY g.date DESC NULLS LAST, m.id DESC
               ) AS copy
        FROM mistakes m JOIN games g ON g.id = m.game_id
        WHERE {_ELIGIBLE}
          AND m.fen NOT IN (SELECT m2.fen FROM deck d JOIN mistakes m2 ON m2.id = d.mistake_id)
    )
    SELECT id FROM candidates WHERE copy = 1
    ORDER BY judgement = '{Judgement.BLUNDER.value}' DESC, date DESC NULLS LAST,
             game_id DESC, ply
"""


class Deck:
    """The practice deck stored in a :class:`MistakeDB`.

    Dates are your local calendar days; pass ``today`` explicitly so a
    session that runs past midnight stays on one day.
    """

    def __init__(self, db: MistakeDB) -> None:
        self.db = db

    def cards(self, today: dt.date, new_per_day: int = NEW_PER_DAY) -> list[Card]:
        """Today's session: the cards due (oldest first), then today's new cards."""
        with self.db.connect() as conn:
            due = [
                r[0]
                for r in conn.execute(
                    "SELECT mistake_id FROM deck WHERE due <= ? ORDER BY due, box, mistake_id",
                    (today.isoformat(),),
                )
            ]
            limit = _new_left(conn, today, new_per_day)
            new = [r[0] for r in conn.execute(f"{_NEW_CARDS} LIMIT ?", (limit,))] if limit else []
            return _load_cards(conn, due + new)

    def card(self, mistake_id: int) -> Card | None:
        with self.db.connect() as conn:
            cards = _load_cards(conn, [mistake_id])
        return cards[0] if cards else None

    def record(self, mistake_id: int, review: Review, today: dt.date) -> Scheduled | None:
        """Grade and reschedule a card you just left; None if it was skipped."""
        grade = review.grade_on_leave()
        if grade is None:
            return None
        with self.db.connect() as conn:
            row = conn.execute("SELECT box FROM deck WHERE mistake_id = ?", (mistake_id,)).fetchone()
            box = row[0] if row else NEW_BOX
            result = schedule(box, grade, today)
            failed = int(grade is Grade.FAIL)
            conn.execute(
                """
                INSERT INTO deck (mistake_id, box, due, lapses) VALUES (?, ?, ?, ?)
                ON CONFLICT (mistake_id) DO UPDATE SET
                    box = excluded.box, due = excluded.due, lapses = deck.lapses + excluded.lapses
                """,
                (mistake_id, result.box, result.due.isoformat(), failed),
            )
            conn.execute(
                """
                INSERT INTO reviews (mistake_id, reviewed_on, grade, wrong_attempts, used_hint,
                                     box_before, box_after, due_after)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mistake_id, today.isoformat(), grade.value, review.wrong_attempts,
                    int(review.used_hint), box, result.box, result.due.isoformat(),
                ),
            )
        return result

    def summary(
        self, today: dt.date, new_per_day: int = NEW_PER_DAY, days_ahead: int = 7
    ) -> DeckSummary:
        with self.db.connect() as conn:
            iso = today.isoformat()
            due = conn.execute("SELECT COUNT(*) FROM deck WHERE due <= ?", (iso,)).fetchone()[0]
            waiting = conn.execute(f"SELECT COUNT(*) FROM ({_NEW_CARDS})").fetchone()[0]
            new_today = min(waiting, _new_left(conn, today, new_per_day))
            boxes = {box: 0 for box in INTERVALS}
            boxes.update(conn.execute("SELECT box, COUNT(*) FROM deck GROUP BY box").fetchall())
            end = today + dt.timedelta(days=days_ahead)
            by_day = dict(
                conn.execute(
                    "SELECT due, COUNT(*) FROM deck WHERE due > ? AND due <= ? GROUP BY due",
                    (iso, end.isoformat()),
                ).fetchall()
            )
            since = (today - dt.timedelta(days=29)).isoformat()
            recent = {
                Grade(g): n
                for g, n in conn.execute(
                    "SELECT grade, COUNT(*) FROM reviews WHERE reviewed_on >= ? GROUP BY grade",
                    (since,),
                )
            }
        upcoming = [
            (day, by_day.get(day.isoformat(), 0))
            for day in (today + dt.timedelta(days=n) for n in range(1, days_ahead + 1))
        ]
        return DeckSummary(due, new_today, waiting, boxes, upcoming, recent)


    def report(self, today: dt.date) -> DeckReport:
        """The deck section of the report: its state, results by pattern, trouble cards."""
        summary = self.summary(today)
        patterns = [t.value for t in Tag if t.group is not TagGroup.CONTEXT]
        marks = ", ".join("?" * len(patterns))
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT t.tag,
                       COUNT(DISTINCT d.mistake_id) AS cards,
                       COUNT(r.id) AS reviews,
                       COUNT(r.id) FILTER (WHERE r.grade = '{Grade.PASS.value}') AS passed
                FROM deck d
                JOIN mistake_tags t ON t.mistake_id = d.mistake_id
                LEFT JOIN reviews r ON r.mistake_id = d.mistake_id
                WHERE t.tag IN ({marks})
                GROUP BY t.tag
                """,
                patterns,
            ).fetchall()
            trouble_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT mistake_id FROM deck WHERE lapses >= ? ORDER BY lapses DESC, due",
                    (TROUBLE_LAPSES,),
                )
            ]
            trouble = _load_cards(conn, trouble_ids)
        practice = [
            PatternPractice(Tag(r["tag"]), r["cards"], r["reviews"], r["passed"]) for r in rows
        ]
        practice.sort(key=lambda p: (-p.reviews, -p.cards, list(Tag).index(p.tag)))
        return DeckReport(summary, practice, trouble)


def _new_left(conn: sqlite3.Connection, today: dt.date, new_per_day: int) -> int:
    """How many more new cards may be introduced ``today``."""
    started = conn.execute(
        "SELECT COUNT(*) FROM reviews WHERE reviewed_on = ? AND box_before = ?",
        (today.isoformat(), NEW_BOX),
    ).fetchone()[0]
    return max(0, new_per_day - started)


def _load_cards(conn: sqlite3.Connection, ids: list[int]) -> list[Card]:
    if not ids:
        return []
    marks = ", ".join("?" * len(ids))
    rows = {
        r["id"]: r
        for r in conn.execute(
            f"""
            SELECT m.*, g.white, g.black, g.date AS game_date, g.link,
                   g.pgn IS NOT NULL AS has_pgn, d.box, d.due, d.lapses
            FROM mistakes m JOIN games g ON g.id = m.game_id
            LEFT JOIN deck d ON d.mistake_id = m.id
            WHERE m.id IN ({marks})
            """,
            ids,
        )
    }
    tags: dict[int, list[Tag]] = {i: [] for i in ids}
    for mistake_id, tag in conn.execute(
        f"SELECT mistake_id, tag FROM mistake_tags WHERE mistake_id IN ({marks})", ids
    ):
        if tag in _TAG_VALUES:
            tags[mistake_id].append(Tag(tag))
    return [
        Card(
            mistake_id=r["id"],
            game_id=r["game_id"],
            box=r["box"] if r["box"] is not None else NEW_BOX,
            due=dt.date.fromisoformat(r["due"]) if r["due"] else None,
            lapses=r["lapses"] or 0,
            mistake=mistake_from_row(r),
            judgement=Judgement(r["judgement"]),
            win_before=r["win_before"],
            win_after=r["win_after"],
            tags=sort_tags(tags[r["id"]]),
            white=r["white"],
            black=r["black"],
            game_date=r["game_date"],
            link=r["link"],
            has_pgn=bool(r["has_pgn"]),
        )
        for r in (rows[i] for i in ids if i in rows)
    ]
