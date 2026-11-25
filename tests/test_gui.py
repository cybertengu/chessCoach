import os

import chess
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtCore = pytest.importorskip("PySide6.QtCore")

from chesscoach.gui.board import BoardWidget  # noqa: E402
from chesscoach.gui.main_window import MainWindow  # noqa: E402
from chesscoach.pgn import read_game  # noqa: E402
from conftest import DATA, requires_stockfish  # noqa: E402


@pytest.fixture(scope="module")
def qapp(tmp_path_factory):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setOrganizationName("chessCoach-tests")
    app.setApplicationName("chessCoach-tests")
    QtCore.QSettings.setPath(
        QtCore.QSettings.Format.NativeFormat, QtCore.QSettings.Scope.UserScope,
        str(tmp_path_factory.mktemp("settings")),
    )
    return app


def wait_until(app, predicate, timeout_ms=60_000):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while not predicate():
        app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
        if timer.elapsed() > timeout_ms:
            raise TimeoutError("condition not met")


def test_board_square_mapping(qapp):
    board = BoardWidget()
    board.resize(400, 400)
    rect = board.square_rect(chess.A1)
    assert board.square_at(rect.center()) == chess.A1
    board.set_flipped(True)
    assert board.square_at(board.square_rect(chess.H8).center()) == chess.H8
    assert board.square_rect(chess.H8).top() > 300  # h8 is bottom-left when flipped


@requires_stockfish
def test_load_game_and_list_mistakes(qapp, monkeypatch):
    window = MainWindow()
    monkeypatch.setattr(window, "_ask_my_color", lambda headers: chess.WHITE)
    window.load_game(read_game((DATA / "hung_queen.pgn").read_text()), "test")
    wait_until(qapp, lambda: window.analysis is not None)

    assert window.move_table.item(2, 0).text() == "Qxe5+??"
    texts = [window.mistakes.item(i).text() for i in range(window.mistakes.count())]
    assert any(t.startswith("3. Qxe5+??") for t in texts)

    # Selecting the mistake jumps the board to that move.
    window.mistakes.setCurrentRow(0)
    assert window.current_ply == 5
    assert "Blunder" in window.details.text()
    window.close()


@requires_stockfish
def test_retry_mode_judges_attempts(qapp, monkeypatch):
    window = MainWindow()
    monkeypatch.setattr(window, "_ask_my_color", lambda headers: chess.WHITE)
    window.load_game(read_game((DATA / "hung_queen.pgn").read_text()), "test")
    wait_until(qapp, lambda: window.analysis is not None)

    window.go_to(5)  # 3. Qxe5+??
    assert window.retry_button.isVisibleTo(window)
    window.start_retry()
    assert window.retry_ply == 5
    assert window.board.board.fen() == window.boards[4].fen()
    assert window.board.interactive_color == chess.WHITE

    # Repeating the blunder is rejected and shows the refutation.
    window.board.move_made.emit(chess.Move.from_uci("h5e5"))
    wait_until(qapp, lambda: "✗" in window.retry_panel.feedback.text())
    assert "Nxe5" in window.retry_panel.feedback.text()

    # A sound developing move fixes it and ticks the mistake off.
    window.start_retry(5)
    window.board.move_made.emit(chess.Move.from_uci("f1c4"))
    wait_until(qapp, lambda: "✓" in window.retry_panel.feedback.text())
    assert 5 in window.solved
    assert window.mistakes.item(0).text().startswith("✓")

    # Navigating away leaves retry mode.
    window.go_to(0)
    assert window.retry_ply is None and window.board.interactive_color is None
    window.close()
