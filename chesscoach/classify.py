"""Turn engine evaluations into judgements: inaccuracy, mistake, blunder.

Uses the same win-probability model as Lichess: a centipawn score is mapped to
a 0-100 "win %", and a move is judged by how much win % it gave away compared
with the engine's best move.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import chess

from .analysis import GameAnalysis, MoveAnalysis

# Logistic fit Lichess uses to convert centipawns to winning chances.
_WIN_COEFF = 0.00368208
# Beyond this the curve is flat anyway; clamping keeps mate scores sane.
_CP_CLAMP = 1500


def win_percent(cp: int) -> float:
    """Winning chances (0-100) for a side with a centipawn advantage of ``cp``."""
    cp = max(-_CP_CLAMP, min(_CP_CLAMP, cp))
    return 50 + 50 * (2 / (1 + math.exp(-_WIN_COEFF * cp)) - 1)


def move_accuracy(win_loss: float) -> float:
    """Lichess-style accuracy (0-100) for a move that lost ``win_loss`` win %."""
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * win_loss) - 3.1669))


class Judgement(Enum):
    BEST = "best"
    GOOD = "good"
    INACCURACY = "inaccuracy"
    MISTAKE = "mistake"
    BLUNDER = "blunder"

    @property
    def symbol(self) -> str:
        return {
            Judgement.INACCURACY: "?!",
            Judgement.MISTAKE: "?",
            Judgement.BLUNDER: "??",
        }.get(self, "")

    @property
    def is_error(self) -> bool:
        return self in (Judgement.INACCURACY, Judgement.MISTAKE, Judgement.BLUNDER)

    @property
    def severity(self) -> int:
        return list(Judgement).index(self)


@dataclass(frozen=True)
class Thresholds:
    """Win % lost (0-100 scale) needed for each judgement.

    Defaults match Lichess (0.1 / 0.2 / 0.3 on its -1..1 winning-chances scale).
    """

    inaccuracy: float = 5.0
    mistake: float = 10.0
    blunder: float = 15.0


DEFAULT_THRESHOLDS = Thresholds()


@dataclass(frozen=True)
class MoveJudgement:
    move: MoveAnalysis
    win_before: float  # mover's win % with best play
    win_after: float  # mover's win % after the move played
    judgement: Judgement

    @property
    def win_loss(self) -> float:
        return max(0.0, self.win_before - self.win_after)

    @property
    def accuracy(self) -> float:
        return move_accuracy(self.win_loss)


def judge_loss(win_loss: float, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> Judgement:
    if win_loss >= thresholds.blunder:
        return Judgement.BLUNDER
    if win_loss >= thresholds.mistake:
        return Judgement.MISTAKE
    if win_loss >= thresholds.inaccuracy:
        return Judgement.INACCURACY
    return Judgement.GOOD


def judge_move(move: MoveAnalysis, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> MoveJudgement:
    color = move.mover
    before = win_percent(move.eval_before.pov(color))
    after = win_percent(move.eval_after.pov(color))
    if move.best_move_uci is not None and move.uci == move.best_move_uci:
        judgement = Judgement.BEST
    else:
        judgement = judge_loss(max(0.0, before - after), thresholds)
    return MoveJudgement(move, before, after, judgement)


def judge_game(
    analysis: GameAnalysis, thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> list[MoveJudgement]:
    return [judge_move(m, thresholds) for m in analysis.moves]


def flagged_moves(
    judgements: list[MoveJudgement],
    color: chess.Color | None = None,
    minimum: Judgement = Judgement.INACCURACY,
) -> list[MoveJudgement]:
    """Moves at least as bad as ``minimum``, optionally for one side only."""
    return [
        j
        for j in judgements
        if j.judgement.is_error
        and j.judgement.severity >= minimum.severity
        and (color is None or j.move.mover == color)
    ]


def side_accuracy(judgements: list[MoveJudgement], color: chess.Color) -> float | None:
    values = [j.accuracy for j in judgements if j.move.mover == color]
    return sum(values) / len(values) if values else None


def player_color(headers: dict[str, str], username: str | None) -> chess.Color | None:
    """Which side ``username`` played in a game, matched case-insensitively."""
    if not username:
        return None
    name = username.strip().lower()
    if headers.get("White", "").lower() == name:
        return chess.WHITE
    if headers.get("Black", "").lower() == name:
        return chess.BLACK
    return None
