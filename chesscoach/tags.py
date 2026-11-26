"""Pattern tags: what kind of mistake a flagged move was.

Tags are worked out with plain rules from facts the engine already produced
(the evaluations, the best line and the opponent's best reply); they never
judge a move themselves. There are three groups:

* **went wrong**: about the move you played and how the opponent punishes it.
  Safe to show at any time, like the refutation arrow.
* **missed**: about the engine's best move. These give the answer away, so
  the GUI keeps them hidden until the puzzle is solved or revealed.
* **context**: the situation the move was played in.

Every number the rules use is in :class:`TagThresholds`. When a rule or a
default changes, bump :data:`TAGGER_VERSION` so stored mistakes get retagged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import chess

from .analysis import Evaluation, MoveAnalysis
from .classify import win_percent

# Bump whenever a rule or a default threshold changes.
TAGGER_VERSION = 1


class TagGroup(Enum):
    WENT_WRONG = "went_wrong"
    MISSED = "missed"
    CONTEXT = "context"


class Tag(Enum):
    """A pattern tag. The value is what the database stores."""

    # Went wrong: your move and the opponent's reply.
    HUNG_PIECE = "hung_piece"
    ALLOWED_MATE = "allowed_mate"
    ALLOWED_BACK_RANK = "allowed_back_rank"
    # Missed: the engine's best move (spoilers).
    MISSED_CAPTURE = "missed_capture"
    MISSED_FORK = "missed_fork"
    MISSED_PIN = "missed_pin"
    MISSED_SKEWER = "missed_skewer"
    MISSED_MATE = "missed_mate"
    MISSED_BACK_RANK = "missed_back_rank"
    # Context.
    OPENING = "opening"
    TIME_TROUBLE = "time_trouble"
    ALREADY_WINNING = "already_winning"
    ALREADY_LOSING = "already_losing"

    @property
    def label(self) -> str:
        return _LABELS[self]

    @property
    def group(self) -> TagGroup:
        if self.value.startswith("missed_"):
            return TagGroup.MISSED
        if self in (Tag.HUNG_PIECE, Tag.ALLOWED_MATE, Tag.ALLOWED_BACK_RANK):
            return TagGroup.WENT_WRONG
        return TagGroup.CONTEXT

    @property
    def spoiler(self) -> bool:
        """True if the tag hints at the best move: hide it until solved or revealed."""
        return self.group is TagGroup.MISSED


_LABELS = {
    Tag.HUNG_PIECE: "Hung a piece",
    Tag.ALLOWED_MATE: "Allowed mate",
    Tag.ALLOWED_BACK_RANK: "Back-rank weakness",
    Tag.MISSED_CAPTURE: "Missed capture",
    Tag.MISSED_FORK: "Missed fork",
    Tag.MISSED_PIN: "Missed pin",
    Tag.MISSED_SKEWER: "Missed skewer",
    Tag.MISSED_MATE: "Missed mate",
    Tag.MISSED_BACK_RANK: "Missed back-rank mate",
    Tag.OPENING: "Opening",
    Tag.TIME_TROUBLE: "Time trouble",
    Tag.ALREADY_WINNING: "Already winning",
    Tag.ALREADY_LOSING: "Already losing",
}


@dataclass(frozen=True)
class TagThresholds:
    """Every number the tag rules use. Change a default → bump TAGGER_VERSION."""

    # "Opening": moves up to and including this move number.
    opening_last_move: int = 12
    # "Time trouble": your clock after the move is below this share of the starting time.
    time_trouble_fraction: float = 0.10
    # "Already winning" / "already losing": your win % before the move
    # (75 % ≈ +3 pawns, 25 % ≈ −3 pawns).
    winning_win_percent: float = 75.0
    losing_win_percent: float = 25.0
    # Material (in pawns) a line must win or lose before a tactic or a hung
    # piece counts.
    material_swing_pawns: int = 2


DEFAULT_TAG_THRESHOLDS = TagThresholds()


def sort_tags(tags) -> list[Tag]:
    """Tags in a stable display order: went wrong, missed, context."""
    order = list(Tag)
    return sorted(tags, key=order.index)


def _mates_for(evaluation: Evaluation, color: chess.Color) -> bool:
    """True if ``color`` has a forced mate (or has already mated)."""
    if evaluation.mate is None:
        return False
    if evaluation.mate == 0:
        return evaluation.pov(color) > 0
    return (evaluation.mate > 0) == (color == chess.WHITE)


def context_tags(
    move: MoveAnalysis,
    base_seconds: int | None,
    thresholds: TagThresholds = DEFAULT_TAG_THRESHOLDS,
) -> set[Tag]:
    tags: set[Tag] = set()
    if move.move_number <= thresholds.opening_last_move:
        tags.add(Tag.OPENING)
    if (
        base_seconds
        and move.clock_seconds is not None
        and move.clock_seconds < thresholds.time_trouble_fraction * base_seconds
    ):
        tags.add(Tag.TIME_TROUBLE)
    win_before = win_percent(move.eval_before.pov(move.mover))
    if win_before >= thresholds.winning_win_percent:
        tags.add(Tag.ALREADY_WINNING)
    elif win_before <= thresholds.losing_win_percent:
        tags.add(Tag.ALREADY_LOSING)
    return tags


def mate_tags(move: MoveAnalysis) -> set[Tag]:
    me, them = move.mover, not move.mover
    tags: set[Tag] = set()
    if _mates_for(move.eval_before, me) and not _mates_for(move.eval_after, me):
        tags.add(Tag.MISSED_MATE)
    if _mates_for(move.eval_after, them) and not _mates_for(move.eval_before, them):
        tags.add(Tag.ALLOWED_MATE)
    return tags


def tag_mistake(
    move: MoveAnalysis,
    refutation_line_san: list[str] | None,
    base_seconds: int | None,
    thresholds: TagThresholds = DEFAULT_TAG_THRESHOLDS,
) -> list[Tag]:
    """All tags for one flagged move, in display order.

    ``refutation_line_san`` is the opponent's best line after ``move`` (None
    if unknown); ``base_seconds`` is the game's starting clock (None if unknown).
    """
    tags = context_tags(move, base_seconds, thresholds) | mate_tags(move)
    return sort_tags(tags)
