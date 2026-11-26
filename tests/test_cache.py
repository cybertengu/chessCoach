import chess.pgn
import pytest

import chesscoach.cache as cache_mod
from chesscoach.cache import AnalysisCache, analyze_with_cache, default_cache_dir, game_key
from chesscoach.pgn import read_game
from conftest import DATA


def _game(name="hung_queen.pgn"):
    return read_game((DATA / name).read_text())


def test_key_depends_on_moves_and_settings_not_headers():
    game = _game()
    renamed = _game()
    renamed.headers["White"] = "Someone else"
    assert game_key(game, nodes=1000) == game_key(renamed, nodes=1000)
    assert game_key(game, nodes=1000) != game_key(game, nodes=2000)
    assert game_key(game, nodes=1000) != game_key(_game("opera_game.pgn"), nodes=1000)


def test_cache_dir_follows_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CHESSCOACH_CACHE_DIR", str(tmp_path / "x"))
    assert default_cache_dir() == tmp_path / "x"
    monkeypatch.delenv("CHESSCOACH_CACHE_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_cache_dir() == tmp_path / "chesscoach" / "analysis"


def test_second_analysis_comes_from_the_cache(monkeypatch, tmp_path):
    cache = AnalysisCache(tmp_path)
    game = _game()
    first = analyze_with_cache(game, cache=cache, nodes=20_000, engine_count=2)
    assert len(list(tmp_path.glob("*.json"))) == 1

    def no_engines(*args, **kwargs):
        raise AssertionError("engines should not start on a cache hit")

    monkeypatch.setattr(cache_mod, "open_engines", no_engines)
    calls = []
    again = analyze_with_cache(
        game, cache=cache, nodes=20_000, progress=lambda d, t: calls.append((d, t))
    )
    assert again == first
    assert calls == [(6, 6)]
    # A different node budget is a different entry.
    with pytest.raises(AssertionError, match="should not start"):
        analyze_with_cache(game, cache=cache, nodes=30_000)


def test_hit_uses_headers_of_the_game_being_opened(tmp_path):
    cache = AnalysisCache(tmp_path)
    analyze_with_cache(_game(), cache=cache, nodes=10_000, engine_count=1)
    renamed = _game()
    renamed.headers["White"] = "Someone else"
    hit = cache.load(renamed, nodes=10_000)
    assert hit is not None and hit.headers["White"] == "Someone else"


def test_unreadable_entry_is_ignored(tmp_path):
    cache = AnalysisCache(tmp_path)
    game = _game()
    (tmp_path / f"{game_key(game, nodes=1000)}.json").write_text("{not json")
    assert cache.load(game, nodes=1000) is None


def test_unwritable_cache_is_skipped(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    cache = AnalysisCache(blocker / "sub")  # parent is a file: mkdir fails
    analysis = analyze_with_cache(_game(), cache=cache, nodes=10_000, engine_count=1)
    assert analysis.moves
    assert cache.load(_game(), nodes=10_000) is None


def test_no_cache_means_nothing_is_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("CHESSCOACH_CACHE_DIR", str(tmp_path))
    analyze_with_cache(_game(), cache=None, nodes=10_000, engine_count=1)
    assert not list(tmp_path.iterdir())


def test_open_engines_can_be_reused(engines, monkeypatch, tmp_path):
    def no_engines(*args, **kwargs):
        raise AssertionError("engines passed in should be used")

    monkeypatch.setattr(cache_mod, "open_engines", no_engines)
    cache = AnalysisCache(tmp_path)
    result = analyze_with_cache(_game(), cache=cache, nodes=20_000, engines=engines)
    assert len(result.moves) == 5
