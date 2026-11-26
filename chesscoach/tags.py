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


# --- motifs ----------------------------------------------------------------

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}
# How many of your own moves in the best line are checked for a fork, pin or
# skewer: the first move, or the one right after a forcing first move.
_TACTIC_MOVES = 2
# A hung piece is judged over the opponent's capture and the replies right
# after it, not the whole line, so later events (a pawn promoting, say) don't count.
_EXCHANGE_PLIES = 4


def material(board: chess.Board, color: chess.Color) -> int:
    """``color``'s material minus the opponent's, in pawns."""
    total = 0
    for piece_type, value in PIECE_VALUES.items():
        total += value * len(board.pieces(piece_type, color))
        total -= value * len(board.pieces(piece_type, not color))
    return total


def _play(board: chess.Board, line_san: list[str]) -> list[chess.Move]:
    """Push the legal prefix of ``line_san`` onto ``board``; returns the moves pushed."""
    moves = []
    for san in line_san:
        try:
            move = board.parse_san(san)
        except ValueError:
            break
        board.push(move)
        moves.append(move)
    return moves


def _is_back_rank_mate(board: chess.Board) -> bool:
    """Checkmate by a rook or queen on the mated king's back rank, the king
    hemmed in by its own pieces."""
    if not board.is_checkmate():
        return False
    loser = board.turn
    king = board.king(loser)
    back_rank = 0 if loser == chess.WHITE else 7
    if king is None or chess.square_rank(king) != back_rank:
        return False
    rank_checkers = [
        sq for sq in board.checkers()
        if board.piece_type_at(sq) in (chess.ROOK, chess.QUEEN)
        and chess.square_rank(sq) == back_rank
    ]
    if not rank_checkers:
        return False
    forward = back_rank + (1 if loser == chess.WHITE else -1)
    file = chess.square_file(king)
    return any(
        board.color_at(chess.square(f, forward)) == loser
        for f in range(max(0, file - 1), min(7, file + 1) + 1)
    )


def _beyond(board: chess.Board, origin: chess.Square, target: chess.Square) -> chess.Square | None:
    """The first occupied square past ``target`` on the line from ``origin``."""
    df = chess.square_file(target) - chess.square_file(origin)
    dr = chess.square_rank(target) - chess.square_rank(origin)
    step_f, step_r = (df > 0) - (df < 0), (dr > 0) - (dr < 0)
    f, r = chess.square_file(target) + step_f, chess.square_rank(target) + step_r
    while 0 <= f < 8 and 0 <= r < 8:
        sq = chess.square(f, r)
        if board.piece_at(sq):
            return sq
        f, r = f + step_f, r + step_r
    return None


def _value(board: chess.Board, square: chess.Square) -> int:
    """Piece value with the king worth more than anything else."""
    piece_type = board.piece_type_at(square)
    return 100 if piece_type == chess.KING else PIECE_VALUES[piece_type]


def _geometry_tags(board: chess.Board, square: chess.Square) -> set[Tag]:
    """Fork, pin or skewer by the piece that just moved to ``square``
    (``board`` has the opponent to move)."""
    me = board.color_at(square)
    them = not me
    attacker_value = _value(board, square)
    tags: set[Tag] = set()

    targets = [
        sq for sq in board.attacks(square)
        if board.color_at(sq) == them
        and board.piece_type_at(sq) != chess.PAWN
        and (
            board.piece_type_at(sq) == chess.KING
            or _value(board, sq) > attacker_value
            or not board.is_attacked_by(them, sq)
        )
    ]
    if len(targets) >= 2:
        tags.add(Tag.MISSED_FORK)

    if board.piece_type_at(square) in (chess.BISHOP, chess.ROOK, chess.QUEEN):
        for front in board.attacks(square):
            if board.color_at(front) != them or board.piece_type_at(front) == chess.PAWN:
                continue
            back = _beyond(board, square, front)
            if back is None or board.color_at(back) != them:
                continue
            front_value, back_value = _value(board, front), _value(board, back)
            if back_value > front_value:
                tags.add(Tag.MISSED_PIN)
            elif front_value > back_value and board.piece_type_at(back) != chess.PAWN:
                tags.add(Tag.MISSED_SKEWER)
    return tags


def went_wrong_tags(
    move: MoveAnalysis,
    refutation_line_san: list[str] | None,
    thresholds: TagThresholds = DEFAULT_TAG_THRESHOLDS,
) -> set[Tag]:
    """Hung piece and back-rank weakness, from the opponent's best reply line."""
    if not refutation_line_san:
        return set()
    board = chess.Board(move.fen_before)
    me = board.turn
    start = material(board, me)
    board.push(chess.Move.from_uci(move.uci))
    first_is_capture = False
    try:
        first_is_capture = board.is_capture(board.parse_san(refutation_line_san[0]))
    except ValueError:
        return set()
    exchange = board.copy()
    _play(exchange, refutation_line_san[:_EXCHANGE_PLIES])
    _play(board, refutation_line_san)
    tags: set[Tag] = set()
    if first_is_capture and start - material(exchange, me) >= thresholds.material_swing_pawns:
        tags.add(Tag.HUNG_PIECE)
    if _is_back_rank_mate(board) and board.turn == me:
        tags.add(Tag.ALLOWED_BACK_RANK)
    return tags


def missed_tags(
    move: MoveAnalysis, thresholds: TagThresholds = DEFAULT_TAG_THRESHOLDS
) -> set[Tag]:
    """Capture, fork, pin, skewer and back-rank mate in the engine's best line."""
    line = move.best_line_san or ([move.best_move_san] if move.best_move_san else [])
    if not line:
        return set()
    board = chess.Board(move.fen_before)
    me = board.turn
    start = material(board, me)
    played = chess.Move.from_uci(move.uci)
    played_capture_square = played.to_square if board.is_capture(played) else None

    moves = _play(board.copy(), line)
    if not moves:
        return set()
    first = moves[0]
    first_is_capture = board.is_capture(first)

    # Geometry alone is not a tactic: the line has to win material after the
    # motif appears. Remember the material at each candidate motif.
    candidates: list[tuple[set[Tag], int]] = []
    for i, line_move in enumerate(moves):
        board.push(line_move)
        if i % 2 == 0 and i // 2 < _TACTIC_MOVES:  # one of your moves
            found = _geometry_tags(board, line_move.to_square)
            if found:
                candidates.append((found, material(board, me)))
    end = material(board, me)

    tags: set[Tag] = set()
    for found, at_motif in candidates:
        if end - at_motif >= thresholds.material_swing_pawns:
            tags |= found
    if _is_back_rank_mate(board) and board.turn != me:
        tags.add(Tag.MISSED_BACK_RANK)
    if (
        first_is_capture
        and not tags
        and end - start >= thresholds.material_swing_pawns
        and first.to_square != played_capture_square
    ):
        tags.add(Tag.MISSED_CAPTURE)
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
    tags = (
        context_tags(move, base_seconds, thresholds)
        | mate_tags(move)
        | went_wrong_tags(move, refutation_line_san, thresholds)
        | missed_tags(move, thresholds)
    )
    return sort_tags(tags)
