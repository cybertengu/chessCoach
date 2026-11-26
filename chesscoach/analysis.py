"""Run Stockfish over every position of a game."""

from __future__ import annotations

import queue
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Callable

import chess
import chess.engine
import chess.pgn

# Centipawn value used to represent "mate" so evaluations can be compared.
MATE_CP = 100_000

# Nodes searched per position. A fixed node budget on a single-threaded engine
# gives the same result on every run, unlike a time limit or multiple threads.
# 200k nodes is roughly depth 17-20 in the middlegame.
DEFAULT_NODES = 200_000


@dataclass(frozen=True)
class Evaluation:
    """An engine evaluation, always from White's point of view.

    ``cp`` folds mates into very large numbers so that comparisons work;
    ``mate`` is set when the engine sees a forced mate (positive: White mates).
    """

    cp: int
    mate: int | None = None

    def pov(self, color: chess.Color) -> int:
        """Centipawns from ``color``'s point of view."""
        return self.cp if color == chess.WHITE else -self.cp

    def __str__(self) -> str:
        if self.mate is not None:
            return f"#{self.mate}" if self.mate != 0 else "#"
        return f"{self.cp / 100:+.2f}"

    @classmethod
    def from_score(cls, score: chess.engine.PovScore) -> "Evaluation":
        white = score.white()
        return cls(cp=white.score(mate_score=MATE_CP), mate=white.mate())


@dataclass(frozen=True)
class PositionInfo:
    evaluation: Evaluation
    best_move: chess.Move | None
    pv: list[chess.Move]


@dataclass
class MoveAnalysis:
    ply: int  # 1-based half-move index
    move_number: int
    color: str  # "white" or "black"
    san: str
    uci: str
    fen_before: str
    eval_before: Evaluation
    eval_after: Evaluation
    best_move_uci: str | None
    best_move_san: str | None
    best_line_san: list[str] = field(default_factory=list)
    clock_seconds: float | None = None

    @property
    def mover(self) -> chess.Color:
        return chess.WHITE if self.color == "white" else chess.BLACK

    @property
    def label(self) -> str:
        """Move label such as ``12. Nf3`` or ``12... Nf6``."""
        dots = "." if self.color == "white" else "..."
        return f"{self.move_number}{dots} {self.san}"


@dataclass
class GameAnalysis:
    headers: dict[str, str]
    moves: list[MoveAnalysis]
    depth: int | None = None  # optional depth cap used for the search
    nodes: int | None = None  # node budget per position

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GameAnalysis":
        moves = []
        for m in data["moves"]:
            m = dict(m)
            m["eval_before"] = Evaluation(**m["eval_before"])
            m["eval_after"] = Evaluation(**m["eval_after"])
            moves.append(MoveAnalysis(**m))
        return cls(
            headers=dict(data["headers"]),
            moves=moves,
            depth=data.get("depth"),
            nodes=data.get("nodes"),
        )


ProgressCallback = Callable[[int, int], None]


def analyze_position(
    engine: chess.engine.SimpleEngine, board: chess.Board, limit: chess.engine.Limit
) -> PositionInfo:
    """Evaluate one position. Finished games are scored without the engine.

    Every search starts from a cleared hash table (``ucinewgame``), so the
    result depends only on the position and ``limit``, not on what the engine
    searched before. Combined with one thread and a node limit this makes
    the evaluation reproducible.
    """
    if board.is_checkmate():
        # The side to move has been mated.
        loser_sign = -1 if board.turn == chess.WHITE else 1
        return PositionInfo(Evaluation(cp=loser_sign * MATE_CP, mate=0), None, [])
    if board.is_game_over(claim_draw=False):
        return PositionInfo(Evaluation(cp=0), None, [])

    info = engine.analyse(board, limit, game=object())
    pv = list(info.get("pv") or [])
    return PositionInfo(Evaluation.from_score(info["score"]), pv[0] if pv else None, pv)


def _line_san(board: chess.Board, moves: list[chess.Move], max_len: int = 8) -> list[str]:
    board = board.copy(stack=False)
    sans: list[str] = []
    for move in moves[:max_len]:
        if move not in board.legal_moves:
            break
        sans.append(board.san(move))
        board.push(move)
    return sans


def _analyze_positions(
    engines: list[chess.engine.SimpleEngine],
    boards: list[chess.Board],
    limit: chess.engine.Limit,
    progress: ProgressCallback | None,
    should_stop: Callable[[], bool] | None,
) -> list[PositionInfo]:
    """Search ``boards`` with a pool of engines, one search per engine at a time."""
    idle: queue.SimpleQueue[chess.engine.SimpleEngine] = queue.SimpleQueue()
    for e in engines:
        idle.put(e)
    canceled = threading.Event()

    def search(board: chess.Board) -> PositionInfo | None:
        if canceled.is_set():
            return None
        e = idle.get()
        try:
            return analyze_position(e, board, limit)
        finally:
            idle.put(e)

    def check_stop() -> None:
        if should_stop and should_stop():
            raise InterruptedError("Analysis canceled")

    check_stop()
    results: list[PositionInfo | None] = [None] * len(boards)
    with ThreadPoolExecutor(max_workers=len(engines)) as pool:
        futures = {pool.submit(search, b): i for i, b in enumerate(boards)}
        try:
            for done, future in enumerate(as_completed(futures), 1):
                results[futures[future]] = future.result()
                if progress:
                    progress(done, len(boards))
                check_stop()
        except BaseException:
            # Skip the positions not started yet; running searches finish
            # (one per engine) before the pool shuts down.
            canceled.set()
            for future in futures:
                future.cancel()
            raise
    return [r for r in results if r is not None]


def analyze_game(
    game: chess.pgn.Game,
    engine: chess.engine.SimpleEngine | Sequence[chess.engine.SimpleEngine],
    *,
    nodes: int = DEFAULT_NODES,
    depth: int | None = None,
    progress: ProgressCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> GameAnalysis:
    """Analyze every move in ``game``.

    Each position is searched once; the evaluation after move *n* is the
    evaluation before move *n + 1*. Each search stops after ``nodes`` nodes
    (or at ``depth``, if given). With a single-threaded engine the result is
    the same on every run.

    ``engine`` may be a list of engines (see :func:`~chesscoach.engine.open_engines`);
    positions are then searched in parallel, one per engine. Because every
    search starts from a cleared hash, the result does not depend on how many
    engines are used. ``progress`` and ``should_stop`` are called from the
    calling thread.
    """
    engines = [engine] if isinstance(engine, chess.engine.SimpleEngine) else list(engine)
    if not engines:
        raise ValueError("analyze_game needs at least one engine")
    limit = chess.engine.Limit(nodes=nodes, depth=depth)
    mainline = list(game.mainline())
    total_positions = len(mainline) + 1

    board = game.board()
    boards: list[chess.Board] = [board.copy(stack=False)]
    for node in mainline:
        board.push(node.move)
        boards.append(board.copy(stack=False))

    positions = _analyze_positions(engines, boards, limit, progress, should_stop)

    moves: list[MoveAnalysis] = []
    for i, node in enumerate(mainline):
        before = boards[i]
        info = positions[i]
        moves.append(
            MoveAnalysis(
                ply=i + 1,
                move_number=before.fullmove_number,
                color="white" if before.turn == chess.WHITE else "black",
                san=before.san(node.move),
                uci=node.move.uci(),
                fen_before=before.fen(),
                eval_before=info.evaluation,
                eval_after=positions[i + 1].evaluation,
                best_move_uci=info.best_move.uci() if info.best_move else None,
                best_move_san=before.san(info.best_move) if info.best_move else None,
                best_line_san=_line_san(before, info.pv),
                clock_seconds=node.clock(),
            )
        )

    return GameAnalysis(headers=dict(game.headers), moves=moves, depth=depth, nodes=nodes)
