"""The "my top mistakes" report: what goes wrong in your games, and how often.

:func:`build_report` reads the mistake database (it never writes to it) and
returns plain dataclasses that the CLI and the GUI render:

* counts by pattern tag, by phase (opening / middlegame / endgame) and by
  time control (bullet / blitz / rapid / daily), per game where that matters;
* a trend by month (or week);
* example positions for each tag and phase, worst first.

Spoilers: a report may count "missed" tags, but an example listed under a
missed tag would hint at the answer. So a missed tag only lists positions
you have already solved or revealed; the others are only counted.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum

from .db import MistakeDB
from .tags import TAGGER_VERSION, Phase, Tag, TagGroup, phase_of, sort_tags


class TimeClass(Enum):
    """Chess.com's speed categories."""

    BULLET = "bullet"
    BLITZ = "blitz"
    RAPID = "rapid"
    DAILY = "daily"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return "Other" if self is TimeClass.UNKNOWN else self.value.capitalize()


def time_class(time_control: str | None) -> TimeClass:
    """Chess.com's rule: base + 40 × increment under 3 min is bullet, under 10 min blitz."""
    value = (time_control or "").strip()
    if re.fullmatch(r"\d+/\d+", value):  # "1/86400": one move per day
        return TimeClass.DAILY
    m = re.fullmatch(r"(\d+)(?:\+(\d+))?", value)
    if not m:
        return TimeClass.UNKNOWN
    estimate = int(m.group(1)) + 40 * int(m.group(2) or 0)
    if estimate < 180:
        return TimeClass.BULLET
    if estimate < 600:
        return TimeClass.BLITZ
    return TimeClass.RAPID


class Period(Enum):
    MONTH = "month"
    WEEK = "week"

    def of(self, date: dt.date) -> str:
        if self is Period.MONTH:
            return f"{date.year}-{date.month:02d}"
        year, week, _ = date.isocalendar()
        return f"{year}-W{week:02d}"


@dataclass(frozen=True)
class Filters:
    since: dt.date | None = None  # games on or after this date
    last_games: int | None = None  # only the most recent N games
    time_class: TimeClass | None = None


@dataclass(frozen=True)
class ReportMistake:
    """One of your stored mistakes, as the report shows it."""

    id: int
    game_id: int
    ply: int
    move_number: int
    color: str
    move_san: str  # the move you played (never the best move)
    judgement: str
    win_loss: float
    tags: tuple[Tag, ...]
    answer_seen: bool  # solved or revealed in a retry
    date: dt.date | None
    opponent: str
    link: str | None
    has_pgn: bool  # the game can be opened from the database

    @property
    def phase(self) -> Phase:
        return phase_of(self.tags)

    @property
    def patterns(self) -> list[Tag]:
        """Went-wrong and missed tags (not context)."""
        return [t for t in self.tags if t.group is not TagGroup.CONTEXT]

    @property
    def visible_tags(self) -> list[Tag]:
        """Tags safe to show now: missed tags only once the answer has been seen."""
        return [t for t in self.tags if self.answer_seen or not t.spoiler]

    @property
    def move_label(self) -> str:
        return f"{self.move_number}{'.' if self.color == 'white' else '...'} {self.move_san}"


@dataclass(frozen=True)
class TagRow:
    tag: Tag
    count: int
    share: float  # % of all mistakes
    avg_loss: float  # average win % lost
    solved: int  # how many you have solved in a retry
    examples: list[ReportMistake]
    hidden: int = 0  # missed tags: unsolved positions not listed (they'd give the answer away)


@dataclass(frozen=True)
class PhaseRow:
    phase: Phase
    count: int
    share: float
    by_judgement: dict[str, int]
    no_pattern: int  # no went-wrong or missed tag: quiet positional / endgame errors
    avg_loss: float
    examples: list[ReportMistake]  # worst first
    no_pattern_examples: list[ReportMistake]


@dataclass(frozen=True)
class TimeClassRow:
    time_class: TimeClass
    games: int
    mistakes: int
    blunders: int

    @property
    def per_game(self) -> float:
        return self.mistakes / self.games if self.games else 0.0

    @property
    def blunders_per_game(self) -> float:
        return self.blunders / self.games if self.games else 0.0


@dataclass(frozen=True)
class TrendRow:
    period: str  # "2026-11" or "2026-W47"
    games: int
    mistakes: int
    blunders: int
    top_patterns: list[tuple[Tag, int]]  # most common went-wrong / missed tags

    @property
    def per_game(self) -> float:
        return self.mistakes / self.games if self.games else 0.0

    @property
    def blunders_per_game(self) -> float:
        return self.blunders / self.games if self.games else 0.0


JUDGEMENTS = ("blunder", "mistake", "inaccuracy")


@dataclass(frozen=True)
class Report:
    filters: Filters
    period: Period
    games: int
    mistakes: list[ReportMistake]
    by_judgement: dict[str, int]
    tags: list[TagRow]
    phases: list[PhaseRow]
    time_classes: list[TimeClassRow]
    trend: list[TrendRow]
    first_date: dt.date | None
    last_date: dt.date | None
    stale_tags: int = 0  # mistakes tagged by older rules (run `chesscoach-cli tag`)
    no_pattern: int = 0  # mistakes with no went-wrong or missed tag

    @property
    def per_game(self) -> float:
        return len(self.mistakes) / self.games if self.games else 0.0


@dataclass(frozen=True)
class _Game:
    id: int
    date: dt.date | None
    time_class: TimeClass


def _parse_date(value: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _worst(mistakes: list[ReportMistake], n: int) -> list[ReportMistake]:
    """The ``n`` biggest drops in win %, most recent first on ties."""
    return sorted(
        mistakes, key=lambda m: (-m.win_loss, -(m.date or dt.date.min).toordinal(), -m.id)
    )[:n]


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def build_report(
    db: MistakeDB | None = None,
    filters: Filters = Filters(),
    *,
    period: Period = Period.MONTH,
    examples: int = 3,
) -> Report:
    """Summarize your stored mistakes. Read-only."""
    db = db or MistakeDB()
    with db.connect() as conn:
        game_rows = conn.execute(
            "SELECT id, date, time_control FROM games ORDER BY date DESC, id DESC"
        ).fetchall()
        mistake_rows = conn.execute(
            """
            SELECT m.id, m.game_id, m.ply, m.move_number, m.color, m.move_san, m.judgement,
                   m.win_loss, m.tags_version, g.date, g.link, g.my_color, g.white, g.black,
                   g.pgn IS NOT NULL AS has_pgn,
                   COALESCE(s.solved, 0) OR COALESCE(s.revealed, 0) AS answer_seen
            FROM mistakes m
            JOIN games g ON g.id = m.game_id
            LEFT JOIN mistake_stats s ON s.mistake_id = m.id
            ORDER BY m.id
            """
        ).fetchall()
        tag_rows = conn.execute("SELECT mistake_id, tag FROM mistake_tags").fetchall()

    known = {t.value for t in Tag}
    tags_of: dict[int, list[Tag]] = {}
    for mistake_id, tag in tag_rows:
        if tag in known:  # skip tags a newer chessCoach may have written
            tags_of.setdefault(mistake_id, []).append(Tag(tag))

    games = [_Game(r["id"], _parse_date(r["date"]), time_class(r["time_control"])) for r in game_rows]
    if filters.since:
        games = [g for g in games if g.date and g.date >= filters.since]
    if filters.time_class:
        games = [g for g in games if g.time_class is filters.time_class]
    if filters.last_games:
        games = games[: filters.last_games]  # already newest first
    by_id = {g.id: g for g in games}

    mistakes: list[ReportMistake] = []
    stale = 0
    for r in mistake_rows:
        if r["game_id"] not in by_id:
            continue
        if r["tags_version"] is None or r["tags_version"] < TAGGER_VERSION:
            stale += 1
        mistakes.append(
            ReportMistake(
                id=r["id"],
                game_id=r["game_id"],
                ply=r["ply"],
                move_number=r["move_number"],
                color=r["color"],
                move_san=r["move_san"],
                judgement=r["judgement"],
                win_loss=r["win_loss"],
                tags=tuple(sort_tags(tags_of.get(r["id"], []))),
                answer_seen=bool(r["answer_seen"]),
                date=by_id[r["game_id"]].date,
                opponent=r["black"] if r["my_color"] == "white" else r["white"],
                link=r["link"],
                has_pgn=bool(r["has_pgn"]),
            )
        )

    total = len(mistakes)

    def share(n: int) -> float:
        return 100.0 * n / total if total else 0.0

    # By tag (phases have their own table, so the phase tags are left out).
    tag_table: list[TagRow] = []
    for tag in Tag:
        if tag in (Tag.OPENING, Tag.ENDGAME):
            continue
        with_tag = [m for m in mistakes if tag in m.tags]
        if not with_tag:
            continue
        listable = [m for m in with_tag if m.answer_seen] if tag.spoiler else with_tag
        tag_table.append(
            TagRow(
                tag=tag,
                count=len(with_tag),
                share=share(len(with_tag)),
                avg_loss=_avg([m.win_loss for m in with_tag]),
                solved=sum(m.answer_seen for m in with_tag),
                examples=_worst(listable, examples),
                hidden=len(with_tag) - len(listable),
            )
        )
    group_order = list(TagGroup)
    tag_table.sort(key=lambda row: (group_order.index(row.tag.group), -row.count))

    phases: list[PhaseRow] = []
    for phase in Phase:
        in_phase = [m for m in mistakes if m.phase is phase]
        quiet = [m for m in in_phase if not m.patterns]
        phases.append(
            PhaseRow(
                phase=phase,
                count=len(in_phase),
                share=share(len(in_phase)),
                by_judgement={j: sum(m.judgement == j for m in in_phase) for j in JUDGEMENTS},
                no_pattern=len(quiet),
                avg_loss=_avg([m.win_loss for m in in_phase]),
                examples=_worst(in_phase, examples),
                no_pattern_examples=_worst(quiet, examples),
            )
        )

    time_classes: list[TimeClassRow] = []
    for tc in TimeClass:
        ids = {g.id for g in games if g.time_class is tc}
        if not ids:
            continue
        mine = [m for m in mistakes if m.game_id in ids]
        time_classes.append(
            TimeClassRow(tc, len(ids), len(mine), sum(m.judgement == "blunder" for m in mine))
        )

    trend: list[TrendRow] = []
    periods = sorted({period.of(g.date) for g in games if g.date})
    for p in periods:
        ids = {g.id for g in games if g.date and period.of(g.date) == p}
        mine = [m for m in mistakes if m.game_id in ids]
        counts = Counter(t for m in mine for t in m.patterns)
        trend.append(
            TrendRow(
                period=p,
                games=len(ids),
                mistakes=len(mine),
                blunders=sum(m.judgement == "blunder" for m in mine),
                top_patterns=sorted(counts.items(), key=lambda kv: (-kv[1], list(Tag).index(kv[0])))[:3],
            )
        )

    dates = [g.date for g in games if g.date]
    return Report(
        filters=filters,
        period=period,
        games=len(games),
        mistakes=mistakes,
        by_judgement={j: sum(m.judgement == j for m in mistakes) for j in JUDGEMENTS},
        tags=tag_table,
        phases=phases,
        time_classes=time_classes,
        trend=trend,
        first_date=min(dates) if dates else None,
        last_date=max(dates) if dates else None,
        stale_tags=stale,
        no_pattern=sum(not m.patterns for m in mistakes),
    )
