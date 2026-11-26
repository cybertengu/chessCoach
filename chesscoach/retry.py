"""Judge a replacement move the player tries at a position where they erred."""

from __future__ import annotations

from dataclasses import dataclass

import chess
import chess.engine

from .analysis import DEFAULT_NODES, Evaluation, MoveAnalysis, _line_san, analyze_position
from .classify import DEFAULT_THRESHOLDS, Judgement, Thresholds, judge_loss, win_percent


@dataclass(frozen=True)
class AttemptResult:
    move_uci: str
    san: str
    judgement: Judgement
    win_before: float  # mover's win % with best play
    win_after: float  # mover's win % after the attempted move
    original_win_after: float  # mover's win % after the move actually played in the game
    evaluation: Evaluation
    reply_line_san: list[str]  # engine's best continuation for the opponent

    @property
    def win_loss(self) -> float:
        return max(0.0, self.win_before - self.win_after)

    @property
    def is_improvement(self) -> bool:
        """Did this attempt keep more winning chances than the game move?"""
        return self.win_after > self.original_win_after + 1.0

    @property
    def solved(self) -> bool:
        """Good enough to count as having fixed the mistake."""
        return self.judgement in (Judgement.BEST, Judgement.GOOD)

    def feedback(self) -> str:
        if self.judgement is Judgement.BEST:
            return f"{self.san} is the engine's top move. Well found!"
        if self.judgement is Judgement.GOOD:
            return f"{self.san} is a good move. It keeps your chances (win {self.win_after:.0f}%)."
        verdict = f"{self.san} is still {('an ' if self.judgement is Judgement.INACCURACY else 'a ')}{self.judgement.value}"
        verdict += f" (win {self.win_before:.0f}% → {self.win_after:.0f}%)"
        if self.is_improvement:
            verdict += ", though better than the game move"
        return verdict + "."


def evaluate_attempt(
    engine: chess.engine.SimpleEngine,
    mistake: MoveAnalysis,
    attempt: chess.Move,
    *,
    nodes: int = DEFAULT_NODES,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> AttemptResult:
    """Score ``attempt`` played in the position before ``mistake``."""
    board = chess.Board(mistake.fen_before)
    if attempt not in board.legal_moves:
        raise ValueError(f"{attempt.uci()} is not legal in this position")

    mover = board.turn
    san = board.san(attempt)
    win_before = win_percent(mistake.eval_before.pov(mover))
    original_after = win_percent(mistake.eval_after.pov(mover))

    board.push(attempt)
    info = analyze_position(engine, board, chess.engine.Limit(nodes=nodes))
    win_after = win_percent(info.evaluation.pov(mover))

    if attempt.uci() == mistake.best_move_uci:
        judgement = Judgement.BEST
    else:
        judgement = judge_loss(max(0.0, win_before - win_after), thresholds)

    return AttemptResult(
        move_uci=attempt.uci(),
        san=san,
        judgement=judgement,
        win_before=win_before,
        win_after=win_after,
        original_win_after=original_after,
        evaluation=info.evaluation,
        reply_line_san=_line_san(board, info.pv, max_len=6),
    )
