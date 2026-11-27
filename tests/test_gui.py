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

    # Opening the same game again adds nothing, and remembers the puzzle's outcome.
    window = open_and_analyze()
    assert "already" in window.statusBar().currentMessage()
    assert 5 in window.solved and 5 in window.revealed
    assert window.mistakes.item(0).text().startswith("✓")
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


@requires_stockfish
def test_tags_hide_the_missed_pattern_until_solved_or_revealed(qapp, monkeypatch):
    from chesscoach.tags import Tag

    window = MainWindow()
    monkeypatch.setattr(window, "_ask_my_color", lambda headers: chess.WHITE)
    window.load_game(read_game((DATA / "hung_queen.pgn").read_text()), "test")
    wait_until(qapp, lambda: window.analysis is not None)
    assert window.tags[5] == [Tag.HUNG_PIECE, Tag.OPENING]
    assert window.mistakes.item(0).text().endswith("Hung a piece · Opening")

    # Pretend the engine's move was a fork: that tag is a spoiler.
    window.tags[5] = [Tag.HUNG_PIECE, Tag.MISSED_FORK, Tag.OPENING]
    window._populate_mistakes()
    window.go_to(5)
    assert "Missed fork" not in window.mistakes.item(0).text()
    assert "Hung a piece" in window.details.text() and "Missed fork" not in window.details.text()
    window.start_retry(5)
    assert "Missed fork" not in window.retry_panel.feedback.text()

    # Solving the puzzle shows it ...
    window.board.move_made.emit(chess.Move.from_uci("f1c4"))
    wait_until(qapp, lambda: "✓" in window.retry_panel.feedback.text())
    assert "Pattern: <b>Missed fork</b>" in window.retry_panel.feedback.text()
    assert "Missed fork" in window.mistakes.item(0).text()

    # ... and so does revealing the answer.
    window.solved.clear()
    window._populate_mistakes()
    assert "Missed fork" not in window.mistakes.item(0).text()
    window.retry_panel.answer_button.click()
    assert "Missed fork" in window.retry_panel.feedback.text()
    assert "Missed fork" in window.mistakes.item(0).text()
    window.close()


@requires_stockfish
def test_opponents_mistakes_show_every_tag(qapp, monkeypatch):
    from chesscoach.tags import Tag

    window = MainWindow()
    monkeypatch.setattr(window, "_ask_my_color", lambda headers: chess.BLACK)
    window.load_game(read_game((DATA / "hung_queen.pgn").read_text()), "test")
    wait_until(qapp, lambda: window.analysis is not None)
    window.tags[5] = [Tag.MISSED_FORK]
    window.go_to(5)  # White's blunder; you played Black, so its answer is no secret
    assert "Tags: Missed fork" in window.details.text()
    window.close()


@requires_stockfish
def test_report_lists_mistakes_and_opens_one_as_a_puzzle(qapp, monkeypatch):
    from chesscoach.db import MistakeDB

    window = MainWindow()
    monkeypatch.setattr(window, "_ask_my_color", lambda headers: chess.WHITE)
    window.load_game(read_game((DATA / "hung_queen.pgn").read_text()), "test")
    wait_until(qapp, lambda: window.analysis is not None)
    best_san = window.judgements[4].move.best_move_san
    window.close()

    window = MainWindow()
    window.show_report()
    text = window._report.browser.toPlainText()
    assert "My top mistakes" in text and "Hung a piece" in text and "By phase" in text
    assert "Retry" in text
    assert best_san not in window._report.browser.toHtml()

    # Clicking Retry opens the game and starts the puzzle without asking for your side.
    def no_asking(headers):
        raise AssertionError("the database knows your side")

    monkeypatch.setattr(window, "_ask_my_color", no_asking)
    with MistakeDB().connect() as conn:
        mistake_id = conn.execute("SELECT id FROM mistakes WHERE ply = 5").fetchone()[0]
    window._report._link_clicked(QtCore.QUrl(f"mistake:{mistake_id}"))
    assert not window._report.isVisible()
    wait_until(qapp, lambda: window.retry_ply == 5)
    assert window.my_color == chess.WHITE
    assert window.board.board.fen() == window.boards[4].fen()
    assert window.board.arrows == []

    # Filters that match nothing say so.
    window._report.time_control.setCurrentIndex(window._report.time_control.findText("Bullet"))
    assert "No games match" in window._report.browser.toPlainText()
    window.close()


def test_report_on_an_empty_database(qapp):
    window = MainWindow()
    window.show_report()
    assert "No games in your mistake database yet" in window._report.browser.toPlainText()
    window.close()


def _stored_hung_queen(qapp, monkeypatch):
    """Analyze the hung-queen game once so its blunder is in the mistake database."""
    window = MainWindow()
    monkeypatch.setattr(window, "_ask_my_color", lambda headers: chess.WHITE)
    window.load_game(read_game((DATA / "hung_queen.pgn").read_text()), "test")
    wait_until(qapp, lambda: window.analysis is not None)
    best = window.judgements[4].move
    window.close()
    return best


@requires_stockfish
def test_practice_session_grades_cards_and_replays_a_missed_one(qapp, monkeypatch):
    import datetime as dt

    from chesscoach.db import MistakeDB
    from chesscoach.deck import Deck
    from chesscoach.gui.practice_window import PracticeWindow
    from chesscoach.tags import Tag

    best = _stored_hung_queen(qapp, monkeypatch)
    today = dt.date(2026, 11, 26)
    window = PracticeWindow(MistakeDB(), today=today)
    window.show()
    window.start()
    card = window.card
    assert card is not None and card.mistake.ply == 5
    assert "Card 1 of 1 · new card" in window.header.text()
    # Set up from the FEN, your side to move, the opponent's last move (2... Nc6) highlighted.
    assert window.board.board.fen() == card.mistake.fen_before
    assert window.board.interactive_color == chess.WHITE
    assert window.board.highlight == (chess.B8, chess.C6)
    assert "Qxe5+??" in window.retry_panel.prompt.text()
    shown = window.retry_panel.prompt.text() + window.retry_panel.feedback.text() + window.info.text()
    assert best.best_move_san not in shown and window.board.arrows == []
    assert "Open the game" not in window.info.text()

    # A "missed" tag stays hidden until the card is solved or revealed in this review.
    object.__setattr__(card, "tags", [Tag.HUNG_PIECE, Tag.MISSED_FORK, Tag.OPENING])
    window._update_info()
    assert "Hung a piece" in window.info.text() and "Missed fork" not in window.info.text()

    window.board.move_made.emit(chess.Move.from_uci("h5e5"))  # the blunder again
    wait_until(qapp, lambda: "✗" in window.retry_panel.feedback.text())
    window.retry_panel.answer_button.click()
    assert best.best_move_san in window.retry_panel.feedback.text()
    assert "Missed fork" in window.info.text() and "Open the game" in window.info.text()
    window.board.move_made.emit(chess.Move.from_uci(best.best_move_uci))
    wait_until(qapp, lambda: "✓" in window.retry_panel.feedback.text())

    # Revealed before solving: a miss. It comes back once at the end, ungraded.
    window.retry_panel.next_button.click()
    assert "Missed" in window.last_result.text()
    assert "Once more, not graded: 1 of 1" in window.header.text()
    assert "Missed fork" not in window.info.text()
    window.board.move_made.emit(chess.Move.from_uci(best.best_move_uci))
    wait_until(qapp, lambda: "✓" in window.retry_panel.feedback.text())
    window.retry_panel.next_button.click()
    assert window.finished
    assert "1 card reviewed" in window.header.text() and "1 missed" in window.header.text()
    assert "Tomorrow: 1 card due" in window.header.text()
    window.close()

    deck = Deck(MistakeDB())
    assert (deck.card(card.mistake_id).box, deck.card(card.mistake_id).due) == (
        1, today + dt.timedelta(days=1),
    )
    with MistakeDB().connect() as conn:
        grades = [r[0] for r in conn.execute("SELECT grade FROM reviews")]
        kinds = [r[0] for r in conn.execute("SELECT kind FROM retries ORDER BY id")]
    assert grades == ["fail"]  # the replay was not graded
    assert kinds == ["attempt", "reveal", "attempt", "attempt"]

    # The next day it's due; finding a good move first try is a pass.
    window = PracticeWindow(MistakeDB(), today=today + dt.timedelta(days=1))
    window.show()
    window.start()
    assert "box 1 of 7" in window.header.text()
    window.board.move_made.emit(chess.Move.from_uci("f1c4"))
    wait_until(qapp, lambda: "✓" in window.retry_panel.feedback.text())
    window.run_key_command("done")  # q ends the session and grades the card
    assert "1 pass" in window.header.text()
    window.close()
    assert deck.card(card.mistake_id).box == 2


def test_practice_with_an_empty_deck(qapp):
    from chesscoach.db import MistakeDB
    from chesscoach.gui.practice_window import PracticeWindow

    window = PracticeWindow(MistakeDB())
    window.show()
    window.start()
    assert window.finished and "Nothing to practice" in window.header.text()
    assert "deck is empty" in window.header.text()
    window.close()


@requires_stockfish
def test_leaving_a_card_untouched_skips_it(qapp, monkeypatch):
    from chesscoach.db import MistakeDB
    from chesscoach.gui.practice_window import PracticeWindow

    _stored_hung_queen(qapp, monkeypatch)
    window = PracticeWindow(MistakeDB())
    window.show()
    window.start()
    window.close()  # closing the window leaves the card: nothing done, nothing recorded
    with MistakeDB().connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_ctrl_p_opens_practice(qapp):
    window = MainWindow()
    window.show_practice()
    assert window._practice.isVisible()
    first = window._practice
    window.show_practice()  # a running session is brought forward, not restarted
    assert window._practice is first
    window._practice.close()
    window.close()


@requires_stockfish
def test_practice_moves_can_be_typed_on_the_command_line(qapp, monkeypatch):
    from PySide6.QtTest import QTest

    from chesscoach.db import MistakeDB
    from chesscoach.gui.practice_window import PracticeWindow

    _stored_hung_queen(qapp, monkeypatch)
    window = PracticeWindow(MistakeDB())
    window.show()
    window.start()
    qapp.processEvents()

    QTest.keyClicks(window.board, ":")
    assert window.command_line.isVisible() and window.command_line.text() == ":"
    # Letters go to the line, not to the puzzle keys (H would be a hint, s the answer).
    QTest.keyClicks(window.command_line, "Hs")
    assert not window.review.used_hint and not window.revealed_now
    window.command_line.setText(":")

    QTest.keyClicks(window.command_line, "Bc")
    assert window.board.preview_move() is None  # not a full move yet
    QTest.keyClicks(window.command_line, "4")
    assert window.board.preview_move() == chess.Move.from_uci("f1c4")  # blue preview arrow
    QTest.keyClick(window.command_line, QtCore.Qt.Key.Key_Return)
    assert not window.command_line.isVisible()
    wait_until(qapp, lambda: "✓" in window.retry_panel.feedback.text())
    assert window.review.grade is not None and window.review.grade.value == "pass"

    # While an attempt is shown, a typed move asks you to undo first.
    window.command_line.setText(":Nf3")
    window._run_command_line()
    assert "undo" in window.message.text()

    # App commands from the main window aren't available here; :q ends the session.
    window.command_line.setText(":report")
    window._run_command_line()
    assert "works in the main window" in window.message.text()
    window.command_line.setText(":q")
    window._run_command_line()
    assert window.finished and "1 pass" in window.header.text()
    window.close()
