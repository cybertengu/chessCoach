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
