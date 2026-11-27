"""The practice deck: your own mistakes as puzzles that come back on a schedule.

Scheduling uses Leitner boxes. A card in box ``n`` comes back after
``INTERVALS[n]`` days. How each review is graded:

* pass: your first move was best or good and you used no hint. The card
  moves up a box; a new card goes straight to box 2.
* hard: you solved it after a wrong try or a hint. The card stays in its
  box (a new card goes to box 1).
* fail: you revealed the answer before solving it, or left the card unsolved
  after a wrong try or a hint. The card drops back to box 1.

Leaving a card before doing anything is a skip: nothing is recorded and it
stays due. This module only holds the rules; the database keeps the cards.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

# Box -> days until the card is due again. Box 0 is a new card.
INTERVALS: dict[int, int] = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30, 6: 60, 7: 120}
MAX_BOX = max(INTERVALS)
NEW_BOX = 0
# A new card that passes on its first review skips box 1.
FIRST_PASS_BOX = 2
# How many cards are introduced to the deck per day.
NEW_PER_DAY = 10


class Grade(Enum):
    PASS = "pass"
    HARD = "hard"
    FAIL = "fail"


@dataclass(frozen=True)
class Scheduled:
    box: int
    due: dt.date


def schedule(box: int, grade: Grade, today: dt.date) -> Scheduled:
    """Where a card in ``box`` goes after a review graded ``grade`` on ``today``."""
    if not NEW_BOX <= box <= MAX_BOX:
        raise ValueError(f"no box {box}")
    if grade is Grade.FAIL:
        new_box = 1
    elif grade is Grade.HARD:
        new_box = max(box, 1)
    elif box == NEW_BOX:
        new_box = FIRST_PASS_BOX
    else:
        new_box = min(box + 1, MAX_BOX)
    return Scheduled(new_box, today + dt.timedelta(days=INTERVALS[new_box]))


class Review:
    """Grades one review of one card from what happens while you work on it.

    The grade is fixed by the first thing that settles it: a solved attempt
    (pass or hard) or revealing the answer (fail). Anything afterwards, like
    playing the revealed move, does not change it.
    """

    def __init__(self) -> None:
        self.wrong_attempts = 0
        self.used_hint = False
        self.grade: Grade | None = None

    @property
    def decided(self) -> bool:
        return self.grade is not None

    def attempt(self, solved: bool) -> None:
        if self.decided:
            return
        if not solved:
            self.wrong_attempts += 1
        elif self.wrong_attempts or self.used_hint:
            self.grade = Grade.HARD
        else:
            self.grade = Grade.PASS

    def hint(self) -> None:
        if not self.decided:
            self.used_hint = True

    def reveal(self) -> None:
        if not self.decided:
            self.grade = Grade.FAIL

    def grade_on_leave(self) -> Grade | None:
        """The grade when you move on from the card; None means it was skipped."""
        if self.grade is not None:
            return self.grade
        if self.wrong_attempts or self.used_hint:
            return Grade.FAIL
        return None
