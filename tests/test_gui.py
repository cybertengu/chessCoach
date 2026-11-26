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

    # Browsing to the mistake explains it but keeps the answer secret.
    window.go_to(5)
    assert "Blunder" in window.details.text()
    best_san = window.judgements[4].move.best_move_san
    assert best_san not in window.details.text()
    assert window.board.arrows == []
    assert not any(best_san in t for t in texts)

    # Selecting the mistake in the list starts the puzzle at the position before it.
    window.mistakes.setCurrentRow(0)
    assert window.retry_ply == 5
    assert window.board.board.fen() == window.boards[4].fen()
    assert window.board.arrows == []
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

    # Undo puts the position back for another try.
    assert window.retry_panel.undo_button.isEnabled()
    window.retry_panel.undo_button.click()
    assert window.board.board.fen() == window.boards[4].fen()
    assert window.board.interactive_color == chess.WHITE
    assert not window.retry_panel.undo_button.isEnabled()

    # A sound developing move fixes it and ticks the mistake off.
    window.board.move_made.emit(chess.Move.from_uci("f1c4"))
    wait_until(qapp, lambda: "✓" in window.retry_panel.feedback.text())
    assert 5 in window.solved
    assert window.mistakes.item(0).text().startswith("✓")

    # Navigating away leaves retry mode.
    window.go_to(0)
    assert window.retry_ply is None and window.board.interactive_color is None
    window.close()


@requires_stockfish
def test_show_correct_move_reveals_answer(qapp, monkeypatch):
    window = MainWindow()
    monkeypatch.setattr(window, "_ask_my_color", lambda headers: chess.WHITE)
    window.load_game(read_game((DATA / "hung_queen.pgn").read_text()), "test")
    wait_until(qapp, lambda: window.analysis is not None)

    window.start_retry(5)
    best = window.judgements[4].move
    assert best.best_move_san not in window.retry_panel.feedback.text()

    window.retry_panel.answer_button.click()
    assert 5 in window.revealed
    assert best.best_move_san in window.retry_panel.feedback.text()
    arrow = window.board.arrows[0]
    assert chess.Move(arrow.start, arrow.end).uci() == best.best_move_uci[:4]
    assert window.mistakes.item(0).text().startswith("👁")
    assert best.best_move_san in window.mistakes.item(0).text()
    # The board stays playable so the revealed move can be tried.
    assert window.board.interactive_color == chess.WHITE
    window.close()


@requires_stockfish
def test_original_move_stays_marked_in_puzzle(qapp, monkeypatch):
    window = MainWindow()
    monkeypatch.setattr(window, "_ask_my_color", lambda headers: chess.WHITE)
    window.load_game(read_game((DATA / "hung_queen.pgn").read_text()), "test")
    wait_until(qapp, lambda: window.analysis is not None)

    window.start_retry(5)  # 3. Qxe5+?? was h5 -> e5
    marked = {sq for sq, _ in window.board.marks}
    assert marked == {chess.H5, chess.E5}
    assert "red" in window.retry_panel.prompt.text()  # a blunder is marked in red

    # Still marked after a (wrong) attempt and after undo.
    window.board.move_made.emit(chess.Move.from_uci("h5e5"))
    wait_until(qapp, lambda: "✗" in window.retry_panel.feedback.text())
    assert {sq for sq, _ in window.board.marks} == {chess.H5, chess.E5}
    window.retry_panel.undo_button.click()
    assert {sq for sq, _ in window.board.marks} == {chess.H5, chess.E5}

    window.exit_retry()
    assert window.board.marks == []
    window.close()


@requires_stockfish
def test_solving_the_last_mistake_does_not_offer_next(qapp, monkeypatch):
    window = MainWindow()
    monkeypatch.setattr(window, "_ask_my_color", lambda headers: chess.WHITE)
    window.load_game(read_game((DATA / "hung_queen.pgn").read_text()), "test")
    wait_until(qapp, lambda: window.analysis is not None)
    assert window._unsolved_plies() == [5]  # Qxe5+?? is White's only mistake

    window.start_retry(5)
    assert not window.retry_panel.next_button.isEnabled()  # nothing else to go to
    window.board.move_made.emit(chess.Move.from_uci("f1c4"))
    wait_until(qapp, lambda: "✓" in window.retry_panel.feedback.text())

    text = window.retry_panel.feedback.text()
    assert "last one" in text
    assert "Next mistake" not in text
    assert not window.retry_panel.next_button.isEnabled()
    window.close()


@requires_stockfish
def test_mistakes_and_retries_are_saved_to_the_database(qapp, monkeypatch):
    from chesscoach.db import MistakeDB

    def open_and_analyze():
        window = MainWindow()
        monkeypatch.setattr(window, "_ask_my_color", lambda headers: chess.WHITE)
        window.load_game(read_game((DATA / "hung_queen.pgn").read_text()), "test")
        wait_until(qapp, lambda: window.analysis is not None)
        return window

    window = open_and_analyze()
    mine = [j.move.ply for j in window.judgements if j.judgement.is_error and j.move.mover == chess.WHITE]
    assert sorted(window.mistake_ids) == mine and 5 in mine
    assert "saved" in window.statusBar().currentMessage()

    window.start_retry(5)
    window.board.move_made.emit(chess.Move.from_uci("h5e5"))  # the blunder again
    wait_until(qapp, lambda: "✗" in window.retry_panel.feedback.text())
    window.retry_panel.answer_button.click()
    window.retry_panel.answer_button.click()  # a second click is not a second reveal
    window.board.move_made.emit(chess.Move.from_uci(window.judgements[4].move.best_move_uci))
    wait_until(qapp, lambda: "✓" in window.retry_panel.feedback.text())
    window.close()

    # Opening the same game again adds nothing.
    window = open_and_analyze()
    assert "already" in window.statusBar().currentMessage()
    window.close()

    with MistakeDB().connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM mistakes").fetchone()[0] == len(mine)
        stats = conn.execute(
            "SELECT s.* FROM mistake_stats s JOIN mistakes m ON m.id = s.mistake_id WHERE m.ply = 5"
        ).fetchone()
        kinds = [r[0] for r in conn.execute("SELECT kind FROM retries ORDER BY id")]
    assert (stats["attempts"], stats["solved"], stats["revealed"]) == (2, 1, 1)
    assert kinds == ["attempt", "reveal", "attempt"]



@requires_stockfish
def test_database_errors_do_not_break_the_window(qapp, monkeypatch, tmp_path):
    from chesscoach.db import MistakeDB

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    window = MainWindow()
    window.db = MistakeDB(blocker / "mistakes.sqlite3")
    monkeypatch.setattr(window, "_ask_my_color", lambda headers: chess.WHITE)
    window.load_game(read_game((DATA / "hung_queen.pgn").read_text()), "test")
    wait_until(qapp, lambda: window.analysis is not None)

    assert "could not save" in window.statusBar().currentMessage()
    assert window.mistakes.count() > 0
    window.start_retry(5)
    window.board.move_made.emit(chess.Move.from_uci("f1c4"))
    wait_until(qapp, lambda: "✓" in window.retry_panel.feedback.text())
    window.close()
