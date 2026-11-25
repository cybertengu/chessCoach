"""Drive the app with the keyboard only."""

import os

import chess
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtCore = pytest.importorskip("PySide6.QtCore")
QtTest = pytest.importorskip("PySide6.QtTest")

from chesscoach.gui.dialogs import SideDialog, VimListWidget, key_help_html  # noqa: E402
from chesscoach.gui.keymap import BINDINGS  # noqa: E402
from chesscoach.gui.main_window import MainWindow  # noqa: E402
from chesscoach.pgn import read_game  # noqa: E402
from conftest import DATA, requires_stockfish  # noqa: E402
from test_gui import qapp, wait_until  # noqa: E402,F401

Key = QtCore.Qt.Key
QTest = QtTest.QTest


def type_keys(widget, keys):
    """Send vim-style key names or single characters."""
    special = {"Esc": Key.Key_Escape, "Enter": Key.Key_Return, "Space": Key.Key_Space}
    for k in keys:
        if k in special:
            QTest.keyClick(widget, special[k])
        else:
            QTest.keyClicks(widget, k)


@pytest.fixture
def window(qapp, monkeypatch):  # noqa: F811
    w = MainWindow()
    monkeypatch.setattr(w, "_ask_my_color", lambda headers: chess.WHITE)
    w.show()
    w.load_game(read_game((DATA / "hung_queen.pgn").read_text()), "test")
    wait_until(qapp, lambda: w.analysis is not None)
    w.go_to(0)
    yield w
    w.close()


@requires_stockfish
def test_vim_navigation(window):
    target = window.move_table  # keys work whichever widget has focus
    type_keys(target, ["l", "l"])
    assert window.current_ply == 2
    type_keys(target, ["h"])
    assert window.current_ply == 1
    type_keys(target, ["G"])
    assert window.current_ply == 5
    type_keys(target, ["g", "g"])
    assert window.current_ply == 0
    type_keys(target, ["3", "l"])
    assert window.current_ply == 3
    type_keys(window.board, ["k"])
    assert window.current_ply == 1
    type_keys(window.board, ["j"])
    assert window.current_ply == 3
    type_keys(window.board, ["2", "G"])  # after White's 2nd move
    assert window.current_ply == 3


@requires_stockfish
def test_solve_puzzle_with_board_cursor(qapp, window):  # noqa: F811
    type_keys(window.board, ["n"])
    assert window.retry_ply == 5
    assert "PUZZLE" in window.mode_label.text()

    # Cursor starts on the king (e1). Pick up the f1 bishop and drop it on c4.
    type_keys(window.board, ["l", "Space", "3", "h", "3", "k", "Space"])
    wait_until(qapp, lambda: "✓" in window.retry_panel.feedback.text())
    assert window.board.board.piece_at(chess.C4) == chess.Piece(chess.BISHOP, chess.WHITE)

    type_keys(window.board, ["u"])
    assert window.board.board.fen() == window.boards[4].fen()
    type_keys(window.board, ["s"])
    assert 5 in window.revealed
    type_keys(window.board, ["q"])
    assert window.retry_ply is None
    assert "NORMAL" in window.mode_label.text()


@requires_stockfish
def test_typed_move_on_command_line(qapp, window):  # noqa: F811
    filter_before = window.filter_box.currentIndex()
    window.start_retry(5)
    type_keys(window.board, [":"])
    assert window.command_line.isVisible()
    # Letters typed into the command line must not trigger shortcuts (c, B...).
    QTest.keyClicks(window.command_line, "Bc4")
    QTest.keyClick(window.command_line, Key.Key_Return)
    assert not window.command_line.isVisible()
    wait_until(qapp, lambda: "✓" in window.retry_panel.feedback.text())
    assert window.filter_box.currentIndex() == filter_before


@requires_stockfish
def test_command_line_errors_and_escape(window):
    type_keys(window.board, [":"])
    QTest.keyClicks(window.command_line, "Nf3")
    QTest.keyClick(window.command_line, Key.Key_Return)
    assert "puzzle" in window.statusBar().currentMessage()
    type_keys(window.board, [":"])
    QTest.keyClick(window.command_line, Key.Key_Escape)
    assert not window.command_line.isVisible()


@requires_stockfish
def test_misc_keys(window):
    flipped = window.board.flipped
    type_keys(window.board, ["f"])
    assert window.board.flipped != flipped
    index = window.filter_box.currentIndex()
    type_keys(window.board, ["c"])
    assert window.filter_box.currentIndex() == (index + 1) % 3
    window.go_to(5)
    type_keys(window.board, ["Enter"])
    assert window.retry_ply == 5
    type_keys(window.board, ["Esc"])
    assert window.retry_ply is None


def test_side_dialog_keys(qapp):  # noqa: F811
    box = SideDialog("me", "you", default_black=False)
    QTest.keyClick(box, "b")
    assert box.clickedButton() is box.black


def test_vim_list_widget(qapp):  # noqa: F811
    lst = VimListWidget()
    lst.addItems(["a", "b", "c"])
    lst.setCurrentRow(0)
    QTest.keyClicks(lst, "jj")
    assert lst.currentRow() == 2
    QTest.keyClicks(lst, "k")
    assert lst.currentRow() == 1
    QTest.keyClicks(lst, "gg")
    assert lst.currentRow() == 0
    QTest.keyClicks(lst, "G")
    assert lst.currentRow() == 2


def test_help_lists_every_binding():
    html = key_help_html()
    for bindings in BINDINGS.values():
        for b in bindings:
            assert b.help in html


@requires_stockfish
def test_preview_before_moving(window):
    window.start_retry(5)
    board = window.board
    # Cursor starts on e1; move to f1 and pick up the bishop. Nothing previewed yet.
    type_keys(board, ["l", "Space"])
    assert board.preview_move() is None
    assert "Holding Bishop on f1" in window.square_label.text()

    # Hovering c4 with the cursor previews Bc4 without playing it.
    type_keys(board, ["3", "h", "3", "k"])
    assert board.preview_move() == chess.Move.from_uci("f1c4")
    assert board.board.piece_at(chess.F1) == chess.Piece(chess.BISHOP, chess.WHITE)
    assert "Bishop f1 → c4" in window.square_label.text() and "(Bc4)" in window.square_label.text()

    # An illegal target shows no preview and says so.
    type_keys(board, ["k"])
    assert board.preview_move() is None
    assert "can't go to c5" in window.square_label.text()


@requires_stockfish
def test_preview_while_typing(window):
    window.start_retry(5)
    type_keys(window.board, [":"])
    QTest.keyClicks(window.command_line, "Nf")
    assert window.board.preview_move() is None
    QTest.keyClicks(window.command_line, "3")
    assert window.board.preview_move() == chess.Move.from_uci("g1f3")
    assert "Knight g1 → f3" in window.square_label.text()
    QTest.keyClick(window.command_line, Key.Key_Escape)
    assert window.board.preview_move() is None
