"""Locating and starting the Stockfish UCI engine."""

from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import chess.engine

ENV_VAR = "CHESSCOACH_STOCKFISH"

# Places Stockfish commonly lives when it is not on PATH.
_FALLBACK_PATHS = (
    "/usr/bin/stockfish",
    "/usr/games/stockfish",
    "/usr/local/bin/stockfish",
)


class EngineNotFoundError(RuntimeError):
    """Raised when no Stockfish binary can be located."""


def find_stockfish(explicit: str | os.PathLike[str] | None = None) -> str:
    """Return the path to a Stockfish binary.

    Lookup order: the ``explicit`` argument, the ``CHESSCOACH_STOCKFISH``
    environment variable, ``stockfish`` on PATH, then a few common locations.
    """
    candidates: list[str] = []
    if explicit:
        candidates.append(str(explicit))
    if os.environ.get(ENV_VAR):
        candidates.append(os.environ[ENV_VAR])
    on_path = shutil.which("stockfish")
    if on_path:
        candidates.append(on_path)
    candidates.extend(_FALLBACK_PATHS)

    for candidate in candidates:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)

    raise EngineNotFoundError(
        "Stockfish was not found. Install it with `sudo dnf install stockfish` "
        f"or set {ENV_VAR} to the binary's path."
    )


@contextmanager
def open_engine(
    path: str | os.PathLike[str] | None = None,
    *,
    threads: int = 1,
    hash_mb: int = 16,
) -> Iterator[chess.engine.SimpleEngine]:
    """Start Stockfish and make sure it is shut down afterwards.

    One thread by default: a multi-threaded search is not reproducible.
    """
    engine = chess.engine.SimpleEngine.popen_uci(find_stockfish(path))
    try:
        options = {"Hash": hash_mb, "Threads": threads}
        engine.configure({k: v for k, v in options.items() if k in engine.options})
        yield engine
    finally:
        engine.quit()
