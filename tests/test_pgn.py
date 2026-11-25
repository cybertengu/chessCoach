import pytest

from chesscoach.pgn import PGNError, read_game, read_games
from conftest import DATA


def test_reads_game_with_headers():
    game = read_game((DATA / "opera_game.pgn").read_text())
    assert game.headers["White"] == "Paul Morphy"
    assert game.end().board().is_checkmate()


def test_reads_multiple_games():
    text = (DATA / "opera_game.pgn").read_text() + "\n\n" + (DATA / "hung_queen.pgn").read_text()
    assert len(read_games(text)) == 2


@pytest.mark.parametrize("text", ["", "hello world", '[Event "x"]\n\n*'])
def test_rejects_text_without_moves(text):
    with pytest.raises(PGNError):
        read_game(text)


def test_rejects_illegal_moves():
    with pytest.raises(PGNError):
        read_game("1. e4 e5 2. Ke3 Ke6 3. Qxh8 *")
