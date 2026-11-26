import pytest

from chesscoach import cli
from chesscoach.db import MistakeDB
from conftest import DATA, requires_stockfish

ARGS = ["analyze", str(DATA / "hung_queen.pgn"), "--nodes", "20000", "--engines", "2"]


def _mistake_count() -> int:
    if not MistakeDB().path.exists():
        return 0
    with MistakeDB().connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM mistakes").fetchone()[0]


@requires_stockfish
def test_analyze_saves_your_mistakes_once(capsys):
    assert cli.main([*ARGS, "--user", "tester"]) == 0
    first = capsys.readouterr().err
    assert "Saved" in first and "new mistake" in first
    stored = _mistake_count()
    assert stored > 0
    with MistakeDB().connect() as conn:
        colors = {r[0] for r in conn.execute("SELECT color FROM mistakes")}
    assert colors == {"white"}  # Tester played White

    assert cli.main([*ARGS, "--user", "tester"]) == 0
    assert "Saved 0 new mistakes" in capsys.readouterr().err
    assert _mistake_count() == stored


@requires_stockfish
def test_analyze_saves_nothing_without_a_known_side_or_with_no_save():
    assert cli.main(ARGS) == 0
    assert cli.main([*ARGS, "--user", "somebody-else"]) == 0
    assert cli.main([*ARGS, "--user", "tester", "--no-save"]) == 0
    assert _mistake_count() == 0


def test_plural_handles_phrases():
    assert cli._plural(1, "new mistake") == "1 new mistake"
    assert cli._plural(2, "new mistake") == "2 new mistakes"


@requires_stockfish
def test_analyze_prints_tags_next_to_mistakes(capsys):
    assert cli.main(ARGS) == 0
    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("3. Qxe5+"))
    assert "[Hung a piece, Opening]" in line


def test_tag_without_engine_tags_and_counts(capsys, monkeypatch):
    from test_db import _forget_tags_and_replies, _record

    db = MistakeDB()
    _record(db)
    _forget_tags_and_replies(db)
    monkeypatch.setattr(cli, "open_engine", lambda *a, **k: pytest.fail("engine started"))
    assert cli.main(["tag", "--no-engine"]) == 0
    out = capsys.readouterr().out
    assert "Tagged 2 mistakes" in out
    assert "  Opening       2\n  Time trouble  1\n" in out
    assert cli.main(["tag", "--no-engine"]) == 0
    assert "Tagged 0 mistakes" in capsys.readouterr().out


@requires_stockfish
def test_tag_searches_missing_replies(capsys):
    from test_db import _forget_tags_and_replies, _record

    db = MistakeDB()
    _record(db)
    _forget_tags_and_replies(db)
    assert cli.main(["tag"]) == 0
    assert "Hung a piece" in capsys.readouterr().out


@requires_stockfish
def test_batch_analyzes_recent_games_and_reports_failures(capsys, monkeypatch):
    from test_batch import _game

    games = [_game(1), _game(2, moves="1. e4 e5 2. Ke3 Zz9")]
    monkeypatch.setattr(cli, "recent_games", lambda username, count: games[:count])
    args = ["batch", "Me", "--nodes", "5000", "--engines", "1"]
    assert cli.main(args) == 1  # one game failed
    out = capsys.readouterr().out
    assert "[1/2]" in out and "new)" in out
    assert "[2/2]" in out and "FAILED" in out
    assert "Analyzed 1 game" in out and "1 failed" in out
    assert _mistake_count() > 0

    assert cli.main([*args, "--count", "1"]) == 0
    assert "already saved" in capsys.readouterr().out


def test_report_prints_every_section_without_spoilers(capsys):
    from test_report import _add_game, _add_mistake

    from chesscoach.tags import Tag

    db = MistakeDB()
    with db.connect() as conn:
        gid = _add_game(conn, "2026-11-20", tc="180+2")
        _add_mistake(conn, gid, 5, [Tag.OPENING, Tag.HUNG_PIECE], "blunder", 40)
        _add_mistake(conn, gid, 41, [], "mistake", 15)
        _add_mistake(conn, gid, 31, [Tag.MISSED_FORK], "mistake", 25)
    assert cli.main(["report"]) == 0
    out = capsys.readouterr().out
    for text in ("1 game", "3 mistakes (3.0 per game)", "Hung a piece", "Missed fork",
                 "By phase", "Middlegame", "Blitz", "2026-11", "No pattern found: middlegame",
                 "1 position not listed"):
        assert text in out, text
    assert "d4" not in out  # the stored best move is never printed

    assert cli.main(["report", "--time-control", "rapid"]) == 0
    assert "No games match" in capsys.readouterr().out


def test_report_on_an_empty_database(capsys):
    assert cli.main(["report"]) == 0
    assert "No games" in capsys.readouterr().out


def test_report_updates_old_tags_first(capsys):
    from test_report import _add_game, _add_mistake

    from chesscoach.tags import TAGGER_VERSION

    db = MistakeDB()
    with db.connect() as conn:
        _add_mistake(conn, _add_game(conn), 30, version=TAGGER_VERSION - 1)
    assert cli.main(["report"]) == 0
    assert "Updated the tags of 1 mistake" in capsys.readouterr().err


def test_deck_shows_todays_puzzles_without_positions(capsys):
    import datetime as dt

    from test_deck import FENS, _failed, _game, _mistake, _passed

    from chesscoach.deck import Deck

    assert cli.main(["deck"]) == 0
    assert "No puzzles yet" in capsys.readouterr().out

    db = MistakeDB()
    with db.connect() as conn:
        g = _game(conn, "2026-11-01")
        a, b, _ = (_mistake(conn, g, ply, fen=FENS[i]) for i, ply in enumerate((1, 3, 5)))
    deck = Deck(db)
    yesterday = dt.date.today() - dt.timedelta(days=1)
    deck.record(a, _passed(), yesterday)
    deck.record(b, _failed(), yesterday)
    assert cli.main(["deck", "--days", "10"]) == 0
    out = capsys.readouterr().out
    for text in ("Today: 1 card due + 1 new = 2 puzzles", "In the deck: 2 cards; 1 more waiting",
                 "  1    1 d     1  #", "  2    3 d     1  #", "2 reviews: 1 pass, 0 hard, 1 fail"):
        assert text in out, text
    assert len(out.split("Coming up")[1].split("Last 30")[0].strip().splitlines()) == 2
    assert "e4" not in out and "a3" not in out  # no moves at all


def test_report_ends_with_the_practice_deck(capsys):
    import datetime as dt

    from test_deck import FENS, _failed, _game, _mistake

    from chesscoach.deck import Deck
    from chesscoach.tags import TAGGER_VERSION

    db = MistakeDB()
    with db.connect() as conn:
        g = _game(conn, "2026-11-01")
        fork = _mistake(conn, g, 3, fen=FENS[1], tags=["missed_fork", "hung_piece"])
        # Tagged by the current rules, so the report keeps these tags.
        conn.execute("UPDATE mistakes SET tags_version = ?", (TAGGER_VERSION,))
    for day in range(3):
        Deck(db).record(fork, _failed(), dt.date.today() - dt.timedelta(days=day + 1))
    assert cli.main(["report"]) == 0
    out = capsys.readouterr().out.split("Practice deck:")[1]
    for text in ("1 card, 0 more waiting", "Hung a piece", "Missed fork", "0%",
                 "Trouble cards (missed 3+ times)", "vs Opponent  2. a3  missed 3x  [Hung a piece]"):
        assert text in out, text
    assert "e4" not in out
