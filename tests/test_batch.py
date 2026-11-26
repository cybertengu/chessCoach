import chess.engine
import pytest

import chesscoach.batch as batch_mod
from chesscoach.batch import GameStatus, analyze_games
from chesscoach.chesscom import ChessComGame
from chesscoach.db import MistakeDB
from chesscoach.engine import EngineNotFoundError, open_engines
from conftest import requires_stockfish

NODES = 5_000


def _game(n: int, moves="1. e4 e5 2. Qh5 Nc6 3. Qxe5+ Nxe5", white="Me", black="Them"):
    url = f"https://www.chess.com/game/live/{n}"
    pgn = f'[White "{white}"]\n[Black "{black}"]\n[Link "{url}"]\n[Result "*"]\n\n{moves} *\n'
    return ChessComGame(
        url=url, pgn=pgn, white=white, black=black, white_result="win", black_result="checkmated",
        white_rating=1000, black_rating=1000, time_class="rapid", time_control="600",
        end_time=None, rules="chess",
    )


class CountingEngines:
    """open_engines() that counts how often engines are started."""

    def __init__(self):
        self.opened = 0

    def __call__(self, count=None, path=None):
        self.opened += 1
        return open_engines(1, path)


def _run(games, **kwargs):
    kwargs.setdefault("open_engines_fn", CountingEngines())
    return analyze_games(games, "me", nodes=NODES, **kwargs)


@requires_stockfish
def test_games_are_saved_then_skipped_unless_redone():
    engines = CountingEngines()
    done = []
    games = [_game(1), _game(2, moves="1. d4 d5 2. Bg5 h6")]
    result = _run(games, open_engines_fn=engines, on_done=lambda i, t, o: done.append((i, t, o.status)))
    assert [o.status for o in result.outcomes] == [GameStatus.SAVED, GameStatus.SAVED]
    assert done == [(1, 2, GameStatus.SAVED), (2, 2, GameStatus.SAVED)]
    assert engines.opened == 1  # started once for the whole batch
    assert result.outcomes[0].mistakes == result.outcomes[0].added > 0  # 3. Qxe5+?? hangs the queen
    with MistakeDB().connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games WHERE pgn IS NOT NULL").fetchone()[0] == 2

    again = _run(games)
    assert again.count(GameStatus.SKIPPED) == 2 and again.added == 0

    engines = CountingEngines()
    redone = _run(games, redo=True, open_engines_fn=engines)
    assert redone.count(GameStatus.SAVED) == 2 and redone.added == 0
    assert engines.opened == 0  # everything came from the analysis cache


@requires_stockfish
def test_a_bad_game_fails_and_the_batch_moves_on():
    games = [_game(1, moves="1. e4 e5 2. Ke3 Zz9"), _game(2, white="A", black="B"), _game(3)]
    result = _run(games)
    statuses = [o.status for o in result.outcomes]
    assert statuses == [GameStatus.FAILED, GameStatus.FAILED, GameStatus.SAVED]
    assert "me did not play" in result.outcomes[1].error
    assert len(result.failed) == 2 and not result.stopped


@requires_stockfish
def test_stopping_keeps_the_games_already_done():
    started = []
    result = _run(
        [_game(1), _game(2, moves="1. d4 d5 2. Bg5 h6"), _game(3, moves="1. c4 e5")],
        on_start=lambda i, t, g: started.append(i),
        should_stop=lambda: len(started) >= 2,
    )
    assert result.stopped
    assert [o.status for o in result.outcomes] == [GameStatus.SAVED]
    with MistakeDB().connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1


def test_missing_stockfish_stops_the_batch():
    def no_stockfish(count=None, path=None):
        raise EngineNotFoundError("Stockfish was not found.")

    with pytest.raises(EngineNotFoundError):
        _run([_game(1)], open_engines_fn=no_stockfish)


@requires_stockfish
def test_engines_are_restarted_after_a_crash(monkeypatch):
    real = batch_mod.analyze_with_cache
    calls = []

    def crash_once(game, **kwargs):
        calls.append(game)
        if len(calls) == 1:
            raise chess.engine.EngineTerminatedError("engine process died")
        return real(game, **kwargs)

    monkeypatch.setattr(batch_mod, "analyze_with_cache", crash_once)
    engines = CountingEngines()
    result = _run([_game(1), _game(2, moves="1. d4 d5 2. Bg5 h6")], open_engines_fn=engines)
    assert [o.status for o in result.outcomes] == [GameStatus.FAILED, GameStatus.SAVED]
    assert "engine error" in result.outcomes[0].error
    assert engines.opened == 2
