import datetime as dt

import pytest

from chesscoach.deck import INTERVALS, MAX_BOX, Grade, Review, Scheduled, schedule

TODAY = dt.date(2026, 11, 26)


def days(n: int) -> dt.date:
    return TODAY + dt.timedelta(days=n)


def test_intervals_grow():
    values = [INTERVALS[b] for b in range(1, MAX_BOX + 1)]
    assert values == sorted(values) and values[0] == 1


@pytest.mark.parametrize(
    "box, grade, expected",
    [
        (0, Grade.PASS, Scheduled(2, days(3))),  # a new card you already know skips box 1
        (0, Grade.HARD, Scheduled(1, days(1))),
        (0, Grade.FAIL, Scheduled(1, days(1))),
        (1, Grade.PASS, Scheduled(2, days(3))),
        (3, Grade.PASS, Scheduled(4, days(14))),
        (3, Grade.HARD, Scheduled(3, days(7))),  # hard repeats the same interval
        (5, Grade.FAIL, Scheduled(1, days(1))),
        (MAX_BOX, Grade.PASS, Scheduled(MAX_BOX, days(INTERVALS[MAX_BOX]))),
    ],
)
def test_schedule(box, grade, expected):
    assert schedule(box, grade, TODAY) == expected


def test_schedule_rejects_unknown_boxes():
    with pytest.raises(ValueError):
        schedule(MAX_BOX + 1, Grade.PASS, TODAY)


def test_first_move_right_is_a_pass():
    review = Review()
    review.attempt(solved=True)
    assert review.grade is Grade.PASS


def test_solving_after_a_wrong_try_is_hard():
    review = Review()
    review.attempt(solved=False)
    review.attempt(solved=False)
    review.attempt(solved=True)
    assert review.grade is Grade.HARD and review.wrong_attempts == 2


def test_solving_after_a_hint_is_hard():
    review = Review()
    review.hint()
    review.attempt(solved=True)
    assert review.grade is Grade.HARD and review.used_hint


def test_revealing_before_solving_fails_and_sticks():
    review = Review()
    review.attempt(solved=False)
    review.reveal()
    review.attempt(solved=True)  # playing the revealed move doesn't count
    assert review.grade is Grade.FAIL
    assert review.grade_on_leave() is Grade.FAIL


def test_revealing_after_solving_keeps_the_pass():
    review = Review()
    review.attempt(solved=True)
    review.reveal()
    assert review.grade is Grade.PASS


def test_leaving_without_a_move_is_a_skip():
    assert Review().grade_on_leave() is None


@pytest.mark.parametrize("help_used", ["wrong try", "hint"])
def test_leaving_unsolved_after_help_fails(help_used):
    review = Review()
    if help_used == "hint":
        review.hint()
    else:
        review.attempt(solved=False)
    assert review.grade is None
    assert review.grade_on_leave() is Grade.FAIL
