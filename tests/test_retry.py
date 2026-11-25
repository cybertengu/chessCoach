import chess
import pytest

from chesscoach.analysis import analyze_game
from chesscoach.classify import Judgement, judge_game
from chesscoach.pgn import read_game
from chesscoach.retry import evaluate_attempt
from conftest import DATA


@pytest.fixture(scope="module")
def qxe5(engine):
    analysis = analyze_game(read_game((DATA / "hung_queen.pgn").read_text()), engine, depth=10)
    j = judge_game(analysis)[-1]
    assert j.judgement is Judgement.BLUNDER
    return j.move


def test_same_blunder_again_is_still_a_blunder(engine, qxe5):
    result = evaluate_attempt(engine, qxe5, chess.Move.from_uci("h5e5"), nodes=100_000)
    assert result.judgement is Judgement.BLUNDER
    assert not result.solved
    assert result.reply_line_san[0] == "Nxe5"
    assert "still a blunder" in result.feedback()


def test_sensible_developing_move_solves_it(engine, qxe5):
    result = evaluate_attempt(engine, qxe5, chess.Move.from_uci("f1c4"), nodes=100_000)  # Bc4
    assert result.solved
    assert result.is_improvement


def test_engine_best_move_is_best(engine, qxe5):
    best = chess.Move.from_uci(qxe5.best_move_uci)
    assert evaluate_attempt(engine, qxe5, best, nodes=100_000).judgement is Judgement.BEST


def test_illegal_attempt_rejected(engine, qxe5):
    with pytest.raises(ValueError):
        evaluate_attempt(engine, qxe5, chess.Move.from_uci("e1e3"))
