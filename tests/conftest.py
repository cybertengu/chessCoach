from pathlib import Path

import pytest

from chesscoach.engine import EngineNotFoundError, find_stockfish, open_engine, open_engines

DATA = Path(__file__).parent / "data"


def _have_stockfish() -> bool:
    try:
        find_stockfish()
        return True
    except EngineNotFoundError:
        return False


requires_stockfish = pytest.mark.skipif(not _have_stockfish(), reason="Stockfish not installed")


@pytest.fixture(autouse=True)
def _private_cache_dir(tmp_path, monkeypatch):
    """Never read or write the user's real analysis cache."""
    monkeypatch.setenv("CHESSCOACH_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(scope="session")
def engine():
    if not _have_stockfish():
        pytest.skip("Stockfish not installed")
    with open_engine(threads=1) as eng:
        yield eng


@pytest.fixture(scope="session")
def engines():
    if not _have_stockfish():
        pytest.skip("Stockfish not installed")
    with open_engines(3) as engs:
        yield engs


@pytest.fixture(autouse=True)
def _private_mistake_db(tmp_path, monkeypatch):
    """Never read or write the user's real mistake database."""
    monkeypatch.setenv("CHESSCOACH_DB", str(tmp_path / "mistakes.sqlite3"))
