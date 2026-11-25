import pytest

from chesscoach.chesscom import API_ROOT, ChessComError, recent_games
from conftest import DATA

PGN = (DATA / "opera_game.pgn").read_text()


def _game(ts, rules="chess", white="Me", black="them", wres="win", bres="checkmated"):
    return {
        "url": f"https://www.chess.com/game/live/{ts}",
        "pgn": PGN,
        "end_time": ts,
        "rules": rules,
        "time_class": "rapid",
        "time_control": "600",
        "white": {"username": white, "result": wres, "rating": 1000},
        "black": {"username": black, "result": bres, "rating": 990},
    }


def make_fetch(pages):
    calls = []

    def fetch(url):
        calls.append(url)
        return pages[url]

    fetch.calls = calls
    return fetch


def test_recent_games_newest_first_and_skips_variants():
    pages = {
        f"{API_ROOT}/me/games/archives": {"archives": ["A/2026/10", "A/2026/11"]},
        "A/2026/11": {"games": [_game(300), _game(400, rules="chess960"), _game(500)]},
        "A/2026/10": {"games": [_game(100), _game(200)]},
    }
    fetch = make_fetch(pages)
    games = recent_games("Me", count=3, fetch=fetch)
    assert [g.end_time.timestamp() for g in games] == [500, 300, 200]


def test_stops_fetching_once_enough_games():
    pages = {
        f"{API_ROOT}/me/games/archives": {"archives": ["A/old", "A/new"]},
        "A/new": {"games": [_game(1), _game(2)]},
        "A/old": {"games": [_game(0)]},
    }
    fetch = make_fetch(pages)
    recent_games("me", count=2, fetch=fetch)
    assert "A/old" not in fetch.calls


def test_result_and_description_from_my_side():
    pages = {
        f"{API_ROOT}/me/games/archives": {"archives": ["A/1"]},
        "A/1": {"games": [_game(1_700_000_000, white="Opp", black="ME", wres="checkmated", bres="win"),
                          _game(1_700_000_001, white="me", black="Opp", wres="stalemate", bres="stalemate")]},
    }
    draw, win = recent_games("me", fetch=make_fetch(pages))
    assert win.result_for("me") == "win" and win.opponent_of("me") == "Opp"
    assert draw.result_for("me") == "draw"
    assert "as Black vs Opp" in win.describe("me")


def test_empty_username_rejected():
    with pytest.raises(ChessComError):
        recent_games("  ", fetch=make_fetch({}))
