import dataclasses

import chess
import pytest

from chesscoach.analysis import Evaluation, MoveAnalysis
from chesscoach.tags import (
    DEFAULT_TAG_THRESHOLDS,
    Tag,
    TagGroup,
    sort_tags,
    tag_mistake,
)


def _move(
    fen: str = chess.STARTING_FEN,
    san: str = "e4",
    best: str | None = None,
    best_line: list[str] | None = None,
    before: Evaluation = Evaluation(0),
    after: Evaluation = Evaluation(-300),
    clock: float | None = None,
    move_number: int | None = None,
) -> MoveAnalysis:
    """A flagged move in ``fen``; ``best`` is the engine's move (SAN), ``best_line`` its line."""
    board = chess.Board(fen)
    move = board.parse_san(san)
    best_move = board.parse_san(best) if best else None
    return MoveAnalysis(
        ply=1,
        move_number=move_number or board.fullmove_number,
        color="white" if board.turn == chess.WHITE else "black",
        san=san,
        uci=move.uci(),
        fen_before=fen,
        eval_before=before,
        eval_after=after,
        best_move_uci=best_move.uci() if best_move else None,
        best_move_san=best,
        best_line_san=best_line or ([best] if best else []),
        clock_seconds=clock,
    )


MIDDLEGAME = "r1bq1rk1/ppp2ppp/2np1n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQ1RK1 w - - 0 20"


def test_every_tag_has_a_label_and_spoilers_are_the_missed_ones():
    for tag in Tag:
        assert tag.label
    assert {t for t in Tag if t.spoiler} == {t for t in Tag if t.group is TagGroup.MISSED}
    assert Tag.HUNG_PIECE.group is TagGroup.WENT_WRONG and not Tag.HUNG_PIECE.spoiler
    assert Tag.MISSED_FORK.spoiler
    assert Tag.TIME_TROUBLE.group is TagGroup.CONTEXT


def test_tags_sort_went_wrong_then_missed_then_context():
    assert sort_tags({Tag.OPENING, Tag.MISSED_MATE, Tag.HUNG_PIECE}) == [
        Tag.HUNG_PIECE, Tag.MISSED_MATE, Tag.OPENING,
    ]


@pytest.mark.parametrize(("move_number", "tagged"), [(1, True), (12, True), (13, False)])
def test_opening_is_up_to_move_12(move_number, tagged):
    tags = tag_mistake(_move(MIDDLEGAME, "h3", move_number=move_number), None, None)
    assert (Tag.OPENING in tags) == tagged


@pytest.mark.parametrize(
    ("clock", "base", "tagged"),
    [(59.0, 600, True), (60.0, 600, False), (300.0, 600, False), (5.0, None, False), (None, 600, False)],
)
def test_time_trouble_is_under_a_tenth_of_the_start_time(clock, base, tagged):
    tags = tag_mistake(_move(MIDDLEGAME, "h3", clock=clock), None, base)
    assert (Tag.TIME_TROUBLE in tags) == tagged


@pytest.mark.parametrize(
    ("cp", "expected"),
    [(400, Tag.ALREADY_WINNING), (-400, Tag.ALREADY_LOSING), (100, None), (-100, None)],
)
def test_already_winning_or_losing_from_the_movers_side(cp, expected):
    context = {Tag.ALREADY_WINNING, Tag.ALREADY_LOSING}
    # White to move: cp is White's view.
    white = tag_mistake(_move(MIDDLEGAME, "h3", before=Evaluation(cp)), None, None)
    assert set(white) & context == ({expected} if expected else set())
    # Black to move: the same score from White's view means the opposite for Black.
    black_fen = MIDDLEGAME.replace(" w ", " b ")
    black = tag_mistake(_move(black_fen, "h6", before=Evaluation(-cp)), None, None)
    assert set(black) & context == ({expected} if expected else set())


def test_thresholds_can_be_adjusted():
    move = _move(MIDDLEGAME, "h3", clock=100.0, move_number=15, before=Evaluation(150))
    assert tag_mistake(move, None, 600) == []
    looser = dataclasses.replace(
        DEFAULT_TAG_THRESHOLDS,
        opening_last_move=15,
        time_trouble_fraction=0.2,
        winning_win_percent=60.0,
    )
    assert tag_mistake(move, None, 600, looser) == [
        Tag.OPENING, Tag.TIME_TROUBLE, Tag.ALREADY_WINNING,
    ]


def test_missed_mate():
    # White mates with Qxf7#; h3 throws it away.
    fen = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
    move = _move(fen, "h3", best="Qxf7#", before=Evaluation(100_000, 1), after=Evaluation(-50))
    assert Tag.MISSED_MATE in tag_mistake(move, None, None)
    assert Tag.ALLOWED_MATE not in tag_mistake(move, None, None)


def test_a_slower_mate_is_not_a_missed_mate():
    fen = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
    move = _move(fen, "h3", best="Qxf7#", before=Evaluation(100_000, 1), after=Evaluation(100_000, 5))
    assert Tag.MISSED_MATE not in tag_mistake(move, None, None)


def test_allowed_mate_for_each_color():
    white = _move(chess.STARTING_FEN, "f3", before=Evaluation(30), after=Evaluation(-100_000, -2))
    assert Tag.ALLOWED_MATE in tag_mistake(white, None, None)
    black_fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    black = _move(black_fen, "f6", before=Evaluation(-30), after=Evaluation(100_000, 3))
    assert Tag.ALLOWED_MATE in tag_mistake(black, None, None)
    # Already getting mated: not something this move allowed.
    lost = _move(chess.STARTING_FEN, "f3", before=Evaluation(-100_000, -5), after=Evaluation(-100_000, -2))
    assert Tag.ALLOWED_MATE not in tag_mistake(lost, None, None)
