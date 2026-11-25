import chess
import pytest

from chesscoach.gui.keymap import (
    BINDINGS,
    Command,
    KeyParser,
    Mode,
    check_no_clashes,
    parse_command_line,
)


def feed_all(parser, tokens, mode=Mode.NORMAL):
    out = []
    for t in tokens:
        cmd = parser.feed(t, mode)
        if cmd:
            out.append(cmd)
    return out


def test_no_key_is_bound_twice():
    assert check_no_clashes() == []


def test_every_binding_has_help():
    for bindings in BINDINGS.values():
        for b in bindings:
            assert b.help and b.keys


def test_simple_keys_and_aliases():
    p = KeyParser()
    assert feed_all(p, ["h", "Left", "l"]) == [Command("prev"), Command("prev"), Command("next")]


def test_counts():
    p = KeyParser()
    assert feed_all(p, ["5", "l"]) == [Command("next", 5)]
    assert feed_all(p, ["1", "2", "j"]) == [Command("down", 12)]
    assert Command("next").n == 1


def test_zero_alone_is_not_a_count():
    p = KeyParser()
    assert feed_all(p, ["0", "l"]) == [Command("next")]
    assert feed_all(p, ["1", "0", "G"]) == [Command("end", 10)]


def test_gg_prefix():
    p = KeyParser()
    assert feed_all(p, ["g", "g"]) == [Command("start")]
    assert feed_all(p, ["3", "g", "g"]) == [Command("start", 3)]
    # "g" followed by something else is dropped, and the next key works normally.
    assert feed_all(p, ["g", "x", "l"]) == [Command("next")]


def test_escape_clears_pending_before_acting():
    p = KeyParser()
    assert feed_all(p, ["5", "Esc", "l"]) == [Command("next")]
    assert feed_all(p, ["Esc"]) == [Command("cancel")]


def test_puzzle_mode_reuses_hjkl_for_cursor():
    p = KeyParser()
    assert feed_all(p, ["h", "Space", "u", "H", "s", "q"], Mode.PUZZLE) == [
        Command("cursor_left"), Command("pick"), Command("undo"),
        Command("hint"), Command("solution"), Command("done"),
    ]
    # gg/G mean nothing while solving.
    assert feed_all(p, ["g", "g", "G"], Mode.PUZZLE) == []


@pytest.mark.parametrize("text,expected", [
    ("e", "open"), (":paste", "paste"), ("cc", "chesscom"), ("q", "quit"), ("help", "help"),
])
def test_line_commands(text, expected):
    assert parse_command_line(text, chess.Board()).command == expected


@pytest.mark.parametrize("text,uci", [("Nf3", "g1f3"), ("e4", "e2e4"), ("e2e4", "e2e4"), ("G1F3", "g1f3")])
def test_line_moves(text, uci):
    assert parse_command_line(text, chess.Board()).move == chess.Move.from_uci(uci)


def test_line_errors():
    assert parse_command_line("Ke5", chess.Board()).error
    assert parse_command_line("Nf3", None).error
    assert parse_command_line("  ", chess.Board()) == parse_command_line("", None)


def test_enter_only_starts_puzzles():
    # Enter means "try a better move" when browsing; in a puzzle it does nothing,
    # so a double-press can't pick up a piece by accident.
    p = KeyParser()
    assert feed_all(p, ["Enter"]) == [Command("retry")]
    assert feed_all(p, ["Enter"], Mode.PUZZLE) == []
    assert not p.handles("Enter", Mode.PUZZLE)
