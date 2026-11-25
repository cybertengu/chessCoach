import chess
import pytest

from chesscoach.analysis import MATE_CP, Evaluation, MoveAnalysis, analyze_game
from chesscoach.classify import (
    Judgement,
    flagged_moves,
    judge_game,
    judge_loss,
    judge_move,
    move_accuracy,
    player_color,
    side_accuracy,
    win_percent,
)
from chesscoach.pgn import read_game
from conftest import DATA


def _move(color, before_cp, after_cp, uci="e2e4", best="d2d4"):
    return MoveAnalysis(
        ply=1, move_number=1, color=color, san="x", uci=uci, fen_before=chess.STARTING_FEN,
        eval_before=Evaluation(before_cp), eval_after=Evaluation(after_cp),
        best_move_uci=best, best_move_san="y",
    )


def test_win_percent_shape():
    assert win_percent(0) == pytest.approx(50)
    assert win_percent(300) > 75
    assert win_percent(-300) == pytest.approx(100 - win_percent(300))
    assert win_percent(MATE_CP) == pytest.approx(win_percent(1500))


@pytest.mark.parametrize(
    "loss,expected",
    [(0, Judgement.GOOD), (4.9, Judgement.GOOD), (5, Judgement.INACCURACY),
     (12, Judgement.MISTAKE), (40, Judgement.BLUNDER)],
)
def test_judge_loss(loss, expected):
    assert judge_loss(loss) is expected


def test_black_perspective():
    # Black to move, eval goes from 0 to +3 for White: Black blundered.
    j = judge_move(_move("black", 0, 300))
    assert j.judgement is Judgement.BLUNDER
    # Same eval swing for White is an improvement, not an error.
    assert judge_move(_move("white", 0, 300)).judgement is Judgement.GOOD


def test_playing_best_move_is_best_even_if_eval_dips():
    j = judge_move(_move("white", 50, -100, uci="d2d4", best="d2d4"))
    assert j.judgement is Judgement.BEST


def test_already_winning_moves_are_not_flagged():
    # +12 -> +8: still completely winning, barely changes win %.
    assert judge_move(_move("white", 1200, 800)).judgement is Judgement.GOOD


def test_accuracy_bounds():
    assert move_accuracy(0) == pytest.approx(100, abs=0.01)
    assert move_accuracy(100) == 0


def test_player_color_case_insensitive():
    headers = {"White": "someone", "Black": "UsagiTengu"}
    assert player_color(headers, "usagitengu") == chess.BLACK
    assert player_color(headers, "nobody") is None
    assert player_color(headers, None) is None


def test_hung_queen_is_a_blunder(engine):
    analysis = analyze_game(read_game((DATA / "hung_queen.pgn").read_text()), engine, depth=10)
    judgements = judge_game(analysis)
    flagged = flagged_moves(judgements, color=chess.WHITE, minimum=Judgement.BLUNDER)
    assert [j.move.san for j in flagged] == ["Qxe5+"]
    assert side_accuracy(judgements, chess.WHITE) < side_accuracy(judgements, chess.BLACK)
