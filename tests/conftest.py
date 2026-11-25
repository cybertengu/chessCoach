from pathlib import Path

import pytest

from chesscoach.engine import EngineNotFoundError, find_stockfish, open_engine

DATA = Path(__file__).parent / "data"


def _have_stockfish() -> bool:
    try:
        find_stockfish()
        return True
    except EngineNotFoundError:
        return False


requires_stockfish = pytest.mark.skipif(not _have_stockfish(), reason="Stockfish not installed")


@pytest.fixture(scope="session")
def engine():
    if not _have_stockfish():
        pytest.skip("Stockfish not installed")
    with open_engine(threads=1) as eng:
        yield eng
