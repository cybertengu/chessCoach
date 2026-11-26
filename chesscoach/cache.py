"""Keep finished analyses on disk so re-opening a game is instant."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
from typing import Callable

import chess.pgn

from .analysis import DEFAULT_NODES, GameAnalysis, ProgressCallback, analyze_game
from .engine import open_engines

ENV_VAR = "CHESSCOACH_CACHE_DIR"

# Bump when the analysis changes in a way that makes old results wrong.
CACHE_VERSION = 1


def default_cache_dir() -> Path:
    """``$CHESSCOACH_CACHE_DIR``, else ``$XDG_CACHE_HOME/chesscoach/analysis``."""
    if os.environ.get(ENV_VAR):
        return Path(os.environ[ENV_VAR])
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "chesscoach" / "analysis"


def game_key(game: chess.pgn.Game, *, nodes: int, depth: int | None = None) -> str:
    """Hash of the starting position, the moves and the search settings.

    Headers and clock times are left out: they do not change the engine's
    verdicts, so the same moves imported twice share one cache entry.
    """
    data = {
        "version": CACHE_VERSION,
        "fen": game.board().fen(),
        "moves": [move.uci() for move in game.mainline_moves()],
        "nodes": nodes,
        "depth": depth,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class AnalysisCache:
    """One JSON file per analysed game, named after :func:`game_key`."""

    def __init__(self, directory: str | os.PathLike[str] | None = None):
        self.directory = Path(directory) if directory else default_cache_dir()

    def _path(self, game: chess.pgn.Game, nodes: int, depth: int | None) -> Path:
        return self.directory / f"{game_key(game, nodes=nodes, depth=depth)}.json"

    def load(
        self, game: chess.pgn.Game, *, nodes: int = DEFAULT_NODES, depth: int | None = None
    ) -> GameAnalysis | None:
        """The cached analysis of ``game``, or None if there is none (or it is unreadable)."""
        try:
            text = self._path(game, nodes, depth).read_text(encoding="utf-8")
            analysis = GameAnalysis.from_dict(json.loads(text))
        except (OSError, ValueError, KeyError, TypeError):
            return None
        # The key ignores headers and clocks, so take them from this game.
        analysis.headers = dict(game.headers)
        for move, node in zip(analysis.moves, game.mainline()):
            move.clock_seconds = node.clock()
        return analysis

    def store(self, game: chess.pgn.Game, analysis: GameAnalysis) -> None:
        """Save ``analysis``. A cache that cannot be written is silently skipped."""
        if analysis.nodes is None:
            return
        path = self._path(game, analysis.nodes, analysis.depth)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(analysis.to_dict()), encoding="utf-8")
            os.replace(tmp, path)  # atomic: readers never see a half-written file
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()


def analyze_with_cache(
    game: chess.pgn.Game,
    *,
    cache: AnalysisCache | None = None,
    nodes: int = DEFAULT_NODES,
    depth: int | None = None,
    engine_count: int | None = None,
    engine_path: str | os.PathLike[str] | None = None,
    progress: ProgressCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> GameAnalysis:
    """Return the cached analysis of ``game``, or analyse it in parallel and cache it.

    Pass ``cache=None`` to always run the engines (nothing is stored).
    """
    if cache is not None:
        cached = cache.load(game, nodes=nodes, depth=depth)
        if cached is not None:
            if progress:
                total = len(cached.moves) + 1
                progress(total, total)
            return cached

    with open_engines(engine_count, engine_path) as engines:
        analysis = analyze_game(
            game, engines, nodes=nodes, depth=depth, progress=progress, should_stop=should_stop
        )
    if cache is not None:
        cache.store(game, analysis)
    return analysis
