import datetime as dt

import pytest

from chesscoach.db import MistakeDB
from chesscoach.deck import (
    INTERVALS,
    MAX_BOX,
    NEW_PER_DAY,
    Deck,
    Grade,
    Review,
    Scheduled,
    schedule,
)
from chesscoach.tags import Tag

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


# --- the deck in the mistake database -------------------------------------

# Distinct positions, White to move.
FENS = [
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
    "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
    "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
    "rnbqkbnr/pppp1ppp/4p3/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
]


def _game(conn, date, white="UsagiTengu", black="Opponent", pgn="1. e4 *"):
    cur = conn.execute(
        "INSERT INTO games (key, white, black, date, my_color, pgn) VALUES (?, ?, ?, ?, 'white', ?)",
        (f"{date}-{white}-{black}-{conn.total_changes}", white, black, date, pgn),
    )
    return cur.lastrowid


def _mistake(conn, game_id, ply, judgement="blunder", fen=FENS[0], tags=(), best="e2e4"):
    cur = conn.execute(
        """
        INSERT INTO mistakes (game_id, ply, move_number, color, fen, move_uci, move_san,
            best_uci, best_san, best_line_san, eval_before_cp, eval_after_cp,
            win_before, win_after, win_loss, judgement)
        VALUES (?, ?, ?, 'white', ?, 'a2a3', 'a3', ?, ?, '', 0, -500, 50, 10, 40, ?)
        """,
        (game_id, ply, (ply + 1) // 2, fen, best, best and "e4", judgement),
    )
    conn.executemany(
        "INSERT INTO mistake_tags (mistake_id, tag) VALUES (?, ?)",
        [(cur.lastrowid, t) for t in tags],
    )
    return cur.lastrowid


def _passed() -> Review:
    review = Review()
    review.attempt(solved=True)
    return review


def _failed() -> Review:
    review = Review()
    review.reveal()
    return review


@pytest.fixture
def db():
    return MistakeDB()


def test_only_mistakes_and_blunders_you_can_still_save_become_cards(db):
    with db.connect() as conn:
        g = _game(conn, "2026-11-01")
        blunder = _mistake(conn, g, 1, "blunder", FENS[0])
        mistake = _mistake(conn, g, 3, "mistake", FENS[1], tags=["already_winning"])
        _mistake(conn, g, 5, "inaccuracy", FENS[2])
        _mistake(conn, g, 7, "blunder", FENS[3], tags=["already_losing"])
        _mistake(conn, g, 9, "blunder", FENS[4], best=None)
    cards = Deck(db).cards(TODAY)
    assert [c.mistake_id for c in cards] == [blunder, mistake]
    assert all(c.is_new and c.due is None for c in cards)
    assert cards[1].tags == [Tag.ALREADY_WINNING]


def test_new_cards_come_blunders_first_then_newest_games(db):
    with db.connect() as conn:
        old, new = _game(conn, "2026-10-01"), _game(conn, "2026-11-20")
        old_blunder = _mistake(conn, old, 1, "blunder", FENS[0])
        new_mistake = _mistake(conn, new, 1, "mistake", FENS[1])
        new_blunder_late = _mistake(conn, new, 9, "blunder", FENS[2])
        new_blunder_early = _mistake(conn, new, 3, "blunder", FENS[3])
    ids = [c.mistake_id for c in Deck(db).cards(TODAY)]
    assert ids == [new_blunder_early, new_blunder_late, old_blunder, new_mistake]


def test_a_repeated_position_is_one_card(db):
    with db.connect() as conn:
        old, new = _game(conn, "2026-10-01"), _game(conn, "2026-11-20")
        _mistake(conn, old, 1, fen=FENS[0])
        newest = _mistake(conn, new, 1, fen=FENS[0])
    deck = Deck(db)
    assert [c.mistake_id for c in deck.cards(TODAY)] == [newest]
    deck.record(newest, _passed(), TODAY)
    # Once the position is in the deck, the other copy never becomes a card.
    assert deck.summary(TODAY).new_waiting == 0


def test_ten_new_cards_a_day(db):
    with db.connect() as conn:
        g = _game(conn, "2026-11-01")
        # Same board, different move numbers: distinct FENs.
        ids = [_mistake(conn, g, ply, fen=f"{FENS[0][:-1]}{ply}") for ply in range(1, 16)]
    deck = Deck(db)
    today = deck.cards(TODAY)
    assert len(today) == NEW_PER_DAY == 10
    for card in today[:4]:
        deck.record(card.mistake_id, _passed(), TODAY)
    # Reviewing some doesn't make room for more the same day ...
    assert [c.mistake_id for c in deck.cards(TODAY)] == ids[4:10]
    assert deck.summary(TODAY).new_today == 6
    # ... but the next day brings ten more.
    assert [c.mistake_id for c in deck.cards(days(1))] == ids[4:14]


def test_reviews_move_cards_between_boxes(db):
    with db.connect() as conn:
        g = _game(conn, "2026-11-01")
        a = _mistake(conn, g, 1, fen=FENS[0])
        b = _mistake(conn, g, 3, fen=FENS[1])
    deck = Deck(db)
    assert deck.record(a, _passed(), TODAY) == Scheduled(2, days(3))
    assert deck.record(b, _failed(), TODAY) == Scheduled(1, days(1))
    assert deck.record(b, Review(), days(1)) is None  # skipped: nothing changes

    tomorrow = deck.cards(days(1), new_per_day=0)
    assert [(c.mistake_id, c.box, c.due, c.lapses) for c in tomorrow] == [(b, 1, days(1), 1)]
    assert deck.record(b, _passed(), days(1)) == Scheduled(2, days(4))
    assert deck.card(b).lapses == 1

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT grade, box_before, box_after, reviewed_on FROM reviews ORDER BY id"
        ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("pass", 0, 2, "2026-11-26"),
        ("fail", 0, 1, "2026-11-26"),
        ("pass", 1, 2, "2026-11-27"),
    ]


def test_overdue_cards_come_first(db):
    with db.connect() as conn:
        g = _game(conn, "2026-11-01")
        a = _mistake(conn, g, 1, fen=FENS[0])
        b = _mistake(conn, g, 3, fen=FENS[1])
    deck = Deck(db)
    deck.record(a, _passed(), TODAY)  # due in 3 days
    deck.record(b, _failed(), days(1))  # due in 2 days
    assert [c.mistake_id for c in deck.cards(days(5), new_per_day=0)] == [b, a]


def test_summary(db):
    with db.connect() as conn:
        g = _game(conn, "2026-11-01", white="Opponent", black="UsagiTengu")
        a = _mistake(conn, g, 1, fen=FENS[0])
        b = _mistake(conn, g, 3, fen=FENS[1])
        _mistake(conn, g, 5, fen=FENS[2])
    deck = Deck(db)
    deck.record(a, _passed(), TODAY)
    deck.record(b, _failed(), TODAY)
    s = deck.summary(TODAY, days_ahead=3)
    assert (s.due, s.new_today, s.new_waiting, s.in_deck) == (0, 1, 1, 2)
    assert s.boxes == {1: 1, 2: 1, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0}
    assert s.upcoming == [(days(1), 1), (days(2), 0), (days(3), 1)]
    assert s.recent == {Grade.PASS: 1, Grade.FAIL: 1}
    assert deck.summary(days(1)).due == 1


def test_card_knows_your_opponent_and_game(db):
    with db.connect() as conn:
        g = _game(conn, "2026-11-01", white="Rival", black="UsagiTengu", pgn=None)
        conn.execute(
            """INSERT INTO mistakes (game_id, ply, move_number, color, fen, move_uci, move_san,
                   best_uci, best_san, eval_before_cp, eval_after_cp, win_before, win_after,
                   win_loss, judgement)
               VALUES (?, 2, 1, 'black', ?, 'a7a6', 'a6', 'e7e5', 'e5', 0, 500, 50, 10, 40,
                       'blunder')""",
            (g, "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"),
        )
    (card,) = Deck(db).cards(TODAY)
    assert card.color is False and card.opponent == "Rival"
    assert card.mistake.best_move_uci == "e7e5" and card.judgement.value == "blunder"
    assert not card.has_pgn and card.game_date == "2026-11-01"
