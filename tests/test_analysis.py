import json

import chess
import pytest

from chesscoach.analysis import MATE_CP, GameAnalysis, analyze_game
from chesscoach.pgn import read_game
from conftest import DATA


def test_analysis_covers_every_move(engine):
    game = read_game((DATA / "opera_game.pgn").read_text())
    calls = []
    result = analyze_game(game, engine, depth=8, progress=lambda d, t: calls.append((d, t)))

    assert len(result.moves) == 33
    assert calls[-1] == (34, 34)
    first, last = result.moves[0], result.moves[-1]
    assert first.label == "1. e4"
    assert last.label == "17. Rd8#"
    # After the final mate White is winning by mate.
    assert last.eval_after.cp == MATE_CP and last.eval_after.mate == 0
    # Evaluations chain: after move n == before move n+1.
    for a, b in zip(result.moves, result.moves[1:]):
        assert a.eval_after == b.eval_before


def test_detects_hung_queen(engine):
    game = read_game((DATA / "hung_queen.pgn").read_text())
    result = analyze_game(game, engine, depth=10)
    qxe5 = result.moves[-1]
    assert qxe5.san == "Qxe5+"
    # Black's Nc6 was fine; Qxe5+ hangs the queen to Nxe5.
    assert qxe5.eval_before.pov(chess.WHITE) - qxe5.eval_after.pov(chess.WHITE) > 500


def test_round_trips_through_json(engine):
    game = read_game((DATA / "hung_queen.pgn").read_text())
    result = analyze_game(game, engine, depth=6)
    restored = GameAnalysis.from_dict(json.loads(json.dumps(result.to_dict())))
    assert restored == result


def test_same_game_gives_same_result(engine):
    # A node limit, one thread and a cleared hash per position make the
    # result independent of what the engine searched before.
    game = read_game((DATA / "hung_queen.pgn").read_text())
    first = analyze_game(game, engine, nodes=30_000)
    analyze_game(read_game((DATA / "opera_game.pgn").read_text()), engine, nodes=30_000)
    second = analyze_game(game, engine, nodes=30_000)
    assert first == second
    assert first.nodes == 30_000


def test_loads_analysis_saved_without_nodes(engine):
    game = read_game((DATA / "hung_queen.pgn").read_text())
    data = analyze_game(game, engine, depth=6).to_dict()
    del data["nodes"]
    assert GameAnalysis.from_dict(data).nodes is None


def test_parallel_engines_give_the_same_result(engine, engines):
    game = read_game((DATA / "opera_game.pgn").read_text())
    calls = []
    parallel = analyze_game(
        game, engines, nodes=20_000, progress=lambda d, t: calls.append((d, t))
    )
    assert parallel == analyze_game(game, engine, nodes=20_000)
    assert [d for d, _ in calls] == list(range(1, 35))


def test_cancel_stops_parallel_analysis(engines):
    game = read_game((DATA / "opera_game.pgn").read_text())
    calls = []
    with pytest.raises(InterruptedError):
        analyze_game(
            game,
            engines,
            nodes=20_000,
            progress=lambda d, t: calls.append(d),
            should_stop=lambda: len(calls) >= 2,
        )
    # Only the searches already running finish after the stop request.
    assert len(calls) == 2
    # The engines are still usable afterwards.
    assert analyze_game(game, engines, depth=4).moves
