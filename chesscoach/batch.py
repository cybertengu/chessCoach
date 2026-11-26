"""Analyze many games in one go and save your mistakes from each.

Used by ``chesscoach-cli batch``. Every game is analyzed (or loaded from the
analysis cache) and saved on its own, so a stopped or failed run keeps the
games it finished, and running it again picks up where it left off.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum

import chess.engine

from .analysis import DEFAULT_NODES, ProgressCallback
from .cache import AnalysisCache, analyze_with_cache
from .chesscom import ChessComGame
from .classify import judge_game, player_color
from .db import DatabaseError, MistakeDB
from .engine import EngineNotFoundError, open_engines
from .pgn import read_game


class GameStatus(Enum):
    SAVED = "saved"  # analyzed (or taken from the cache) and saved
    SKIPPED = "skipped"  # already in the mistake database
    FAILED = "failed"


@dataclass(frozen=True)
class GameOutcome:
    game: ChessComGame
    status: GameStatus
    mistakes: int = 0  # your mistakes stored for this game
    added: int = 0  # how many of them are new
    error: str | None = None


@dataclass
class BatchResult:
    outcomes: list[GameOutcome] = field(default_factory=list)
    stopped: bool = False  # stopped before every game was done

    def count(self, status: GameStatus) -> int:
        return sum(o.status is status for o in self.outcomes)

    @property
    def added(self) -> int:
        return sum(o.added for o in self.outcomes)

    @property
    def failed(self) -> list[GameOutcome]:
        return [o for o in self.outcomes if o.status is GameStatus.FAILED]


# (index from 1, total, game)
GameStarted = Callable[[int, int, ChessComGame], None]
# (index from 1, total, outcome)
GameFinished = Callable[[int, int, GameOutcome], None]

# Errors that would hit every game the same way: stop the whole batch.
_FATAL = (EngineNotFoundError, DatabaseError)


def analyze_games(
    games: Sequence[ChessComGame],
    username: str,
    *,
    db: MistakeDB | None = None,
    cache: AnalysisCache | None = None,
    nodes: int = DEFAULT_NODES,
    engine_count: int | None = None,
    engine_path: str | os.PathLike[str] | None = None,
    redo: bool = False,
    should_stop: Callable[[], bool] | None = None,
    on_start: GameStarted | None = None,
    on_progress: ProgressCallback | None = None,
    on_done: GameFinished | None = None,
    open_engines_fn: Callable[..., contextlib.AbstractContextManager] = open_engines,
) -> BatchResult:
    """Analyze ``games`` in order and save ``username``'s mistakes from each.

    Games already in the mistake database are skipped unless ``redo``. A
    game that can't be read or analyzed is marked failed and the batch moves
    on; a missing Stockfish or an unusable database stops it (the error is
    raised). ``should_stop`` (checked between positions) and Ctrl-C stop the
    batch cleanly: the result says so and lists the games done so far.

    Engines are started once, on the first game that isn't in the analysis
    cache, and reused for the rest; they are restarted after an engine crash.
    """
    db = db or MistakeDB()
    cache = cache or AnalysisCache()
    result = BatchResult()
    total = len(games)
    with contextlib.ExitStack() as outer:
        engine_stack = contextlib.ExitStack()
        outer.callback(engine_stack.close)
        engines: list[chess.engine.SimpleEngine] | None = None

        for index, game in enumerate(games, 1):
            if should_stop and should_stop():
                result.stopped = True
                break
            if on_start:
                on_start(index, total, game)
            try:
                args = (game, username, db, cache, nodes, redo, should_stop, on_progress)
                outcome = _one_game(*args, engines)
                if outcome is None:  # not in the analysis cache: start the engines
                    engines = engine_stack.enter_context(open_engines_fn(engine_count, engine_path))
                    outcome = _one_game(*args, engines)
            except (InterruptedError, KeyboardInterrupt):
                result.stopped = True
                break
            except _FATAL:
                raise
            except chess.engine.EngineError as exc:
                # A crashed engine is no use for the next game: start fresh ones.
                engine_stack.close()
                engine_stack = contextlib.ExitStack()
                outer.callback(engine_stack.close)
                engines = None
                outcome = GameOutcome(game, GameStatus.FAILED, error=f"engine error: {exc}")
            except Exception as exc:  # noqa: BLE001 - one bad game must not end the batch
                outcome = GameOutcome(game, GameStatus.FAILED, error=str(exc) or type(exc).__name__)
            result.outcomes.append(outcome)
            if on_done:
                on_done(index, total, outcome)
    return result


def _one_game(
    game: ChessComGame,
    username: str,
    db: MistakeDB,
    cache: AnalysisCache,
    nodes: int,
    redo: bool,
    should_stop: Callable[[], bool] | None,
    on_progress: ProgressCallback | None,
    engines: list[chess.engine.SimpleEngine] | None,
) -> GameOutcome | None:
    """Analyze and save one game. None if it needs engines and none are open yet."""
    pgn_game = read_game(game.pgn)
    color = player_color(dict(pgn_game.headers), username)
    if color is None:
        return GameOutcome(game, GameStatus.FAILED, error=f"{username} did not play this game")
    if not redo and db.is_stored(pgn_game):
        return GameOutcome(game, GameStatus.SKIPPED)
    if engines is None and cache.load(pgn_game, nodes=nodes) is None:
        return None
    analysis = analyze_with_cache(
        pgn_game,
        cache=cache,
        nodes=nodes,
        engines=engines,
        progress=on_progress,
        should_stop=should_stop,
    )
    recorded = db.record_game(analysis, judge_game(analysis), color, pgn=game.pgn)
    return GameOutcome(
        game, GameStatus.SAVED, mistakes=len(recorded.mistake_ids), added=recorded.added
    )
