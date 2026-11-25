import chess
import pytest

from chesscoach.engine import EngineNotFoundError, find_stockfish, open_engine


def _have_stockfish() -> bool:
    try:
        find_stockfish()
        return True
    except EngineNotFoundError:
        return False


requires_stockfish = pytest.mark.skipif(not _have_stockfish(), reason="Stockfish not installed")


def test_missing_engine_gives_helpful_error(monkeypatch, tmp_path):
    monkeypatch.setenv("CHESSCOACH_STOCKFISH", str(tmp_path / "nope"))
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr("chesscoach.engine._FALLBACK_PATHS", ())
    with pytest.raises(EngineNotFoundError, match="dnf install stockfish"):
        find_stockfish()


@requires_stockfish
def test_engine_finds_mate_in_one():
    # Scholar's mate pattern: Qxf7# is the only mate.
    board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
    with open_engine(threads=1) as engine:
        result = engine.play(board, chess.engine.Limit(depth=8))
    assert board.san(result.move) == "Qxf7#"
