import datetime as dt

import chess
import pytest

from chesscoach.db import MistakeDB
from chesscoach.report import Filters, Period, TimeClass, build_report, time_class
from chesscoach.tags import TAGGER_VERSION, Phase, Tag

_game_ids = iter(range(1, 1000))


def _add_game(conn, date="2026-11-20", tc="600", color="white", pgn="1. e4 *"):
    gid = next(_game_ids)
    conn.execute(
        """INSERT INTO games (id, key, link, white, black, date, time_control, my_color, pgn)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (gid, f"k{gid}", f"https://chess.com/game/{gid}", "Me" if color == "white" else "Opp",
         "Opp" if color == "white" else "Me", date, tc, color, pgn),
    )
    return gid


def _add_mistake(conn, gid, ply, tags=(), judgement="mistake", loss=20.0, seen=None,
                 version=TAGGER_VERSION):
    cur = conn.execute(
        """INSERT INTO mistakes (game_id, ply, move_number, color, fen, move_uci, move_san,
               best_uci, best_san, eval_before_cp, eval_after_cp, win_before, win_after,
               win_loss, judgement, tags_version)
           VALUES (?, ?, ?, 'white', ?, 'e2e4', 'e4', 'd2d4', 'd4', 0, -200, 50, 50 - ?,
                   ?, ?, ?)""",
        (gid, ply, (ply + 1) // 2, chess.STARTING_FEN, loss, loss, judgement, version),
    )
    mid = cur.lastrowid
    conn.executemany("INSERT INTO mistake_tags VALUES (?, ?)", [(mid, t.value) for t in tags])
    if seen == "solved":
        conn.execute("INSERT INTO retries (mistake_id, kind, solved) VALUES (?, 'attempt', 1)", (mid,))
    elif seen == "revealed":
        conn.execute("INSERT INTO retries (mistake_id, kind) VALUES (?, 'reveal')", (mid,))
    return mid


@pytest.fixture
def db():
    db = MistakeDB()
    with db.connect() as conn:
        rapid = _add_game(conn, "2026-10-05")
        blitz = _add_game(conn, "2026-11-20", tc="180+2")
        _add_game(conn, "2026-11-21", tc="60")  # a clean bullet game
        _add_mistake(conn, rapid, 5, [Tag.OPENING, Tag.HUNG_PIECE], "blunder", 40)
        _add_mistake(conn, rapid, 41, [], "mistake", 15)  # quiet middlegame error
        _add_mistake(conn, blitz, 61, [Tag.ENDGAME], "inaccuracy", 8)
        _add_mistake(conn, blitz, 63, [Tag.ENDGAME, Tag.MISSED_FORK, Tag.TIME_TROUBLE], "blunder", 35)
        _add_mistake(conn, blitz, 31, [Tag.MISSED_FORK], "mistake", 25, seen="revealed")
    return db


@pytest.mark.parametrize(
    ("tc", "expected"),
    [("60", TimeClass.BULLET), ("120+1", TimeClass.BULLET), ("180+2", TimeClass.BLITZ),
     ("300", TimeClass.BLITZ), ("600", TimeClass.RAPID), ("1/86400", TimeClass.DAILY),
     ("-", TimeClass.UNKNOWN), (None, TimeClass.UNKNOWN)],
)
def test_time_class_follows_chess_com(tc, expected):
    assert time_class(tc) is expected


def test_overview(db):
    report = build_report(db)
    assert report.games == 3 and len(report.mistakes) == 5
    assert report.per_game == pytest.approx(5 / 3)
    assert report.by_judgement == {"blunder": 2, "mistake": 2, "inaccuracy": 1}
    assert report.no_pattern == 2
    assert (report.first_date, report.last_date) == (dt.date(2026, 10, 5), dt.date(2026, 11, 21))
    assert report.stale_tags == 0


def test_tags_counted_but_missed_examples_only_once_seen(db):
    rows = {row.tag: row for row in build_report(db).tags}
    assert Tag.OPENING not in rows and Tag.ENDGAME not in rows  # phases cover those
    hung = rows[Tag.HUNG_PIECE]
    assert (hung.count, hung.hidden, len(hung.examples)) == (1, 0, 1)
    fork = rows[Tag.MISSED_FORK]
    assert fork.count == 2 and fork.share == pytest.approx(40.0)
    assert fork.avg_loss == pytest.approx(30.0)
    # Only the revealed one is listed; the unsolved one would hint at a fork.
    assert [m.ply for m in fork.examples] == [31] and fork.hidden == 1 and fork.done == 1
    # Went wrong first, then missed, then context.
    assert [r.tag for r in build_report(db).tags] == [
        Tag.HUNG_PIECE, Tag.MISSED_FORK, Tag.TIME_TROUBLE,
    ]
    # The unsolved fork listed under "time trouble" doesn't show its missed tag.
    trouble = rows[Tag.TIME_TROUBLE].examples[0]
    assert trouble.visible_tags == [Tag.ENDGAME, Tag.TIME_TROUBLE]


def test_phases_keep_mistakes_with_no_pattern(db):
    phases = {row.phase: row for row in build_report(db).phases}
    assert [p for p in phases] == [Phase.OPENING, Phase.MIDDLEGAME, Phase.ENDGAME]
    assert (phases[Phase.OPENING].count, phases[Phase.OPENING].no_pattern) == (1, 0)
    middle = phases[Phase.MIDDLEGAME]
    assert (middle.count, middle.no_pattern) == (2, 1)
    assert [m.ply for m in middle.no_pattern_examples] == [41]
    end = phases[Phase.ENDGAME]
    assert end.by_judgement == {"blunder": 1, "mistake": 0, "inaccuracy": 1}
    assert [m.ply for m in end.examples] == [63, 61]  # worst first


def test_time_classes_per_game(db):
    rows = {row.time_class: row for row in build_report(db).time_classes}
    assert set(rows) == {TimeClass.BULLET, TimeClass.BLITZ, TimeClass.RAPID}
    assert rows[TimeClass.BULLET].per_game == 0
    assert (rows[TimeClass.BLITZ].mistakes, rows[TimeClass.BLITZ].blunders_per_game) == (3, 1.0)


def test_trend_by_month_and_week(db):
    months = build_report(db).trend
    assert [(t.period, t.games, t.mistakes) for t in months] == [("2026-10", 1, 2), ("2026-11", 2, 3)]
    assert months[1].per_game == 1.5
    assert months[1].top_patterns == [(Tag.MISSED_FORK, 2)]
    weeks = build_report(db, period=Period.WEEK).trend
    assert [t.period for t in weeks] == ["2026-W41", "2026-W47"]


def test_filters(db):
    assert build_report(db, Filters(since=dt.date(2026, 11, 1))).games == 2
    recent = build_report(db, Filters(last_games=1))
    assert recent.games == 1 and recent.mistakes == []
    blitz = build_report(db, Filters(time_class=TimeClass.BLITZ))
    assert blitz.games == 1 and len(blitz.mistakes) == 3


def test_stale_tags_are_counted():
    db = MistakeDB()
    with db.connect() as conn:
        _add_mistake(conn, _add_game(conn), 30, version=TAGGER_VERSION - 1)
    assert build_report(db).stale_tags == 1


def test_empty_database():
    report = build_report(MistakeDB())
    assert report.games == 0 and report.per_game == 0 and report.trend == []


def test_report_html_has_the_deck_section_without_spoilers():
    import datetime as dt

    pytest.importorskip("PySide6")
    from test_deck import FENS, _failed, _game, _mistake

    from chesscoach.db import MistakeDB
    from chesscoach.deck import Deck
    from chesscoach.gui.report_dialog import deck_html

    db = MistakeDB()
    assert deck_html(Deck(db).report(dt.date.today())) == ""
    with db.connect() as conn:
        g = _game(conn, "2026-11-01")
        fork = _mistake(conn, g, 3, fen=FENS[1], tags=["missed_fork", "hung_piece"])
    for day in range(3):
        Deck(db).record(fork, _failed(), dt.date.today() - dt.timedelta(days=day + 1))
    html = deck_html(Deck(db).report(dt.date.today()))
    for text in ("Practice deck", "1 day", "120 days", "Practice by pattern", "Trouble cards",
                 f"mistake:{fork}", "missed 3×", "Practice them with Ctrl+P"):
        assert text in html, text
    trouble = html.split("Trouble cards")[1]
    assert "Missed fork" not in trouble and "e4" not in html
