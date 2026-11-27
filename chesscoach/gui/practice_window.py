"""The practice window: today's cards from the practice deck, one puzzle at a time.

Each card is a stored mistake, set up straight from its FEN; no game has to
be loaded or analyzed. The puzzle works like a retry in the main window:
make a move, the engine judges it, undo and try again, ask for a hint or
for the answer. How it went grades the card (see chesscoach.deck), and a
failed card comes back once more at the end of the session, ungraded.

Spoilers: "missed" tags are hidden until you solve or reveal the card in
this review, even if you solved it in an earlier one.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter

import chess
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..classify import Judgement
from ..db import DatabaseError, MistakeDB
from ..deck import INTERVALS, Card, Deck, Grade, Review
from ..engine import open_engine
from ..pgn import PGNError, read_game
from ..retry import AttemptResult, evaluate_attempt
from . import style
from .board import Arrow
from .dialogs import HelpDialog
from .interactive import InteractiveBoard
from .keymap import KeyParser, Mode, parse_command_line
from .keys import CommandLine, key_token
from .retry_panel import RetryPanel
from .tasks import TaskThread

GREEN = "#2e8b37"


def _plural(n: int, word: str) -> str:
    return f"1 {word}" if n == 1 else f"{n} {word}s"


def _days(n: int) -> str:
    return "tomorrow" if n == 1 else f"in {n} days"


class PracticeWindow(QDialog):
    """One practice session. Call :meth:`start` after showing it."""

    open_mistake = Signal(int)  # mistakes.id: open its game in the main window

    def __init__(
        self, db: MistakeDB, parent: QWidget | None = None, today: dt.date | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Practice")
        self.resize(1100, 720)
        self.db = db
        self.deck = Deck(db)
        self.today = today or dt.date.today()

        # The session: today's cards, then a second look at the failed ones.
        self.queue: list[tuple[Card, bool]] = []  # (card, is an ungraded replay)
        self.graded_total = 0
        self.index = -1
        self.card: Card | None = None
        self.replay = False
        self.review = Review()
        self.solved_now = False  # in this showing of the card
        self.revealed_now = False
        self.previous_move: chess.Move | None = None
        self.grades: Counter[Grade] = Counter()
        self.skipped = 0
        self.finished = False

        self._attempt_thread: TaskThread | None = None
        self._attempt_token = 0
        self.keys = KeyParser()

        self._build()
        QApplication.instance().installEventFilter(self)

    def _build(self) -> None:
        self.board = InteractiveBoard()
        self.board.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.board.move_made.connect(self._attempt_made)

        self.header = QLabel()
        self.header.setTextFormat(Qt.TextFormat.RichText)
        self.header.setWordWrap(True)

        self.retry_panel = RetryPanel()
        self.retry_panel.next_button.setText("Next card  (n)")
        self.retry_panel.next_button.setToolTip("Move on to the next card")
        self.retry_panel.exit_button.setToolTip("End the session (q or Esc)")
        self.retry_panel.hint_requested.connect(self.hint)
        self.retry_panel.answer_requested.connect(self.reveal)
        self.retry_panel.undo_requested.connect(self.undo)
        self.retry_panel.next_requested.connect(self.next_card)
        self.retry_panel.exit_requested.connect(self.finish)

        self.info = QLabel()
        self.info.setTextFormat(Qt.TextFormat.RichText)
        self.info.setWordWrap(True)
        self.info.linkActivated.connect(self._link)

        self.last_result = QLabel()
        self.last_result.setTextFormat(Qt.TextFormat.RichText)
        self.last_result.setWordWrap(True)

        self.close_button = QPushButton("Close  (q)")
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.clicked.connect(self.close)
        self.close_button.hide()

        # Vim-style ':' line: type a move (:Nc4 or :b1c3), or :help, :flip, :q.
        self.command_line = CommandLine(self.close_command_line)
        self.command_line.returnPressed.connect(self._run_command_line)
        self.command_line.textEdited.connect(self._preview_typed_move)
        self.command_line.hide()
        self.message = QLabel()
        self.message.setWordWrap(True)

        right = QVBoxLayout()
        right.addWidget(self.header)
        right.addWidget(self.retry_panel)
        right.addWidget(self.info)
        right.addStretch(1)
        right.addWidget(self.last_result)
        right.addWidget(self.message)
        right.addWidget(self.command_line)
        right.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignRight)

        layout = QHBoxLayout(self)
        layout.addWidget(self.board, 3)
        layout.addLayout(right, 2)

    # --- the session ------------------------------------------------------

    def start(self) -> None:
        try:
            cards = self.deck.cards(self.today)
        except DatabaseError as exc:
            cards = []
            self.last_result.setText(f"⚠ Could not read the practice deck: {exc}")
        self.queue = [(card, False) for card in cards]
        self.graded_total = len(cards)
        self.index = -1
        self._advance()

    def next_card(self) -> None:
        if self.finished:
            return
        self._leave_card()
        self._advance()

    def finish(self) -> None:
        """End the session early (or close the window once it has ended)."""
        if self.finished:
            self.close()
            return
        self._leave_card()
        self._show_summary()

    def _advance(self) -> None:
        self.index += 1
        if self.index < len(self.queue):
            self._show_card(*self.queue[self.index])
        else:
            self._show_summary()

    def _leave_card(self) -> None:
        """Grade the card you're leaving and put it back in the schedule."""
        card, self.card = self.card, None
        self._attempt_token += 1  # an attempt still being judged no longer counts
        if card is None or self.replay:
            return
        grade = self.review.grade_on_leave()
        if grade is None:
            self.skipped += 1
            self.last_result.setText("Skipped: that card stays due.")
            return
        self.grades[grade] += 1
        try:
            scheduled = self.deck.record(card.mistake_id, self.review, self.today)
        except DatabaseError as exc:
            self.last_result.setText(f"⚠ Could not save the review: {exc}")
            return
        if scheduled is not None:
            days = (scheduled.due - self.today).days
            text = {
                Grade.PASS: f"<b style='color:{GREEN}'>Pass</b>: next look {_days(days)}.",
                Grade.HARD: f"<b>Hard</b>: next look {_days(days)}.",
                Grade.FAIL: "<b style='color:#d23c3c'>Missed</b>: it comes back at the end "
                            f"of this session and again {_days(days)}.",
            }[grade]
            self.last_result.setText(f"Last card: {text}")
        if grade is Grade.FAIL:
            self.queue.append((card, True))

    def _show_card(self, card: Card, replay: bool) -> None:
        self.card, self.replay = card, replay
        self.review = Review()
        self.solved_now = self.revealed_now = False
        self.previous_move = self._previous_move(card)
        self.board.set_flipped(card.color == chess.BLACK)
        self._set_up_board()

        if replay:
            replays = len(self.queue) - self.graded_total
            n = self.index - self.graded_total + 1
            where = f"Once more, not graded: {n} of {replays}"
        else:
            state = "new card" if card.is_new else f"box {card.box} of {max(INTERVALS)}"
            where = f"Card {self.index + 1} of {self.graded_total} · {state}"
        self.header.setText(f"<b>Practice</b> · {where}")

        m, j = card.mistake, card.judgement
        side = "White" if card.color == chess.WHITE else "Black"
        color = style.judgement_color(j).name()
        played = f"{m.move_number}{'.' if card.color == chess.WHITE else '…'} {m.san}"
        self.retry_panel.set_prompt(
            f"<h3>Find a better move for {side}</h3>"
            f"Against {card.opponent}, {card.game_date or 'date unknown'}. "
            f"You played <b style='color:{color}'>{played}{j.symbol}</b> ({j.value}, "
            f"win chance {card.win_before:.0f}% → {card.win_after:.0f}%). It stays marked in "
            f"<span style='color:{color}'>{style.judgement_color_name(j)}</span> on the board."
        )
        self.retry_panel.set_feedback("Make your move on the board.")
        for button in (self.retry_panel.hint_button, self.retry_panel.answer_button,
                       self.retry_panel.next_button):
            button.setEnabled(True)
        self.retry_panel.show()
        self.close_button.hide()
        self._update_info()
        self.keys.reset()
        self.board.setFocus()

    def _set_up_board(self) -> None:
        card = self.card
        assert card is not None
        self.board.set_hint(None)
        self.board.set_position(
            chess.Board(card.mistake.fen_before), last_move=self.previous_move,
            arrows=self._answer_arrows(),
        )
        color = QColor(style.judgement_color(card.judgement))
        color.setAlpha(150)
        played = chess.Move.from_uci(card.mistake.uci)
        self.board.set_marks([(played.from_square, color), (played.to_square, color)])
        self.board.set_interactive(card.color)
        self.retry_panel.set_undo_enabled(False)

    def _previous_move(self, card: Card) -> chess.Move | None:
        """Your opponent's move just before the puzzle, from the stored game if there is one."""
        if not card.has_pgn or card.mistake.ply < 2:
            return None
        try:
            row = self.db.mistake_game(card.mistake_id)
            game = read_game(row["pgn"]) if row is not None else None
        except (DatabaseError, PGNError):
            return None
        if game is None:
            return None
        board = game.board()
        moves = list(game.mainline_moves())[: card.mistake.ply - 1]
        if len(moves) != card.mistake.ply - 1:
            return None
        for move in moves:
            board.push(move)
        return moves[-1] if board.fen() == card.mistake.fen_before else None

    def _show_summary(self) -> None:
        self.finished = True
        self.card = None
        self.board.set_interactive(None)
        self.board.set_marks([])
        self.board.set_hint(None)
        self.retry_panel.hide()
        self.info.setText("")
        self.close_button.show()

        reviewed = sum(self.grades.values())
        lines = []
        if reviewed or self.skipped:
            lines.append("<h3>Practice done</h3>")
            lines.append(
                f"{_plural(reviewed, 'card')} reviewed: "
                f"<b style='color:{GREEN}'>{self.grades[Grade.PASS]} pass</b> · "
                f"<b>{self.grades[Grade.HARD]} hard</b> · "
                f"<b style='color:#d23c3c'>{self.grades[Grade.FAIL]} missed</b>"
            )
            if self.skipped:
                lines.append(f"{_plural(self.skipped, 'card')} skipped (still due).")
        else:
            lines.append("<h3>Nothing to practice right now</h3>")
        left = sum(1 for _card, replay in self.queue[max(self.index, 0) + 1:] if not replay)
        if left:
            lines.append(f"{_plural(left, 'card')} left for later today: open Practice again.")
        try:
            summary = self.deck.summary(self.today + dt.timedelta(days=1))
        except DatabaseError:
            summary = None
        if summary is not None:
            if summary.due or summary.new_today:
                lines.append(
                    f"Tomorrow: {_plural(summary.due, 'card')} due + {summary.new_today} new."
                )
            elif not summary.in_deck and not summary.new_waiting:
                lines.append(
                    "Your practice deck is empty. Analyze some games first: your mistakes "
                    "and blunders become the cards."
                )
        title, *rest = lines
        self.header.setText(title + "<br>".join(rest))

    # --- one puzzle ---------------------------------------------------------

    def _attempt_made(self, move: chess.Move) -> None:
        card = self.card
        if card is None:
            return
        board = chess.Board(card.mistake.fen_before)
        if move not in board.legal_moves:
            return
        san = board.san(move)
        board.push(move)
        self.board.set_interactive(None)
        self.board.set_hint(None)
        self.board.set_position(board, last_move=move)
        self.retry_panel.set_feedback(f"Checking <b>{san}</b>…")
        self.retry_panel.set_undo_enabled(True)

        self._attempt_token += 1
        token = self._attempt_token
        mistake = card.mistake

        def work(_progress, _stop):
            with open_engine() as engine:
                return evaluate_attempt(engine, mistake, move)

        thread = TaskThread(work, self)
        thread.succeeded.connect(lambda result: self._attempt_judged(token, board, result))
        thread.failed.connect(lambda msg: self.retry_panel.set_feedback(f"⚠ {msg}"))
        self._attempt_thread = thread
        thread.start()

    def _attempt_judged(self, token: int, board: chess.Board, result: AttemptResult) -> None:
        card = self.card
        if token != self._attempt_token or card is None:
            return
        self.review.attempt(result.solved)
        try:
            self.db.record_attempt(card.mistake_id, result)
        except DatabaseError as exc:
            self.last_result.setText(f"⚠ Could not save the attempt: {exc}")

        if result.solved:
            self.solved_now = True
            text = f"<span style='color:{GREEN}'><b>✓ {result.feedback()}</b></span>"
            text += self._missed_text()
            if result.judgement is not Judgement.BEST and not self.revealed_now:
                text += "<br>The engine liked another move a little more. <b>Undo</b> to look for it."
            last = self.index == len(self.queue) - 1
            text += "<br>Press <b>n</b> to " + ("finish." if last else "go to the next card.")
            self.retry_panel.set_feedback(text)
            self._update_info()
            return

        color = style.judgement_color(result.judgement).name()
        text = f"<span style='color:{color}'><b>✗ {result.feedback()}</b></span>"
        arrows = []
        if result.reply_line_san:
            text += f"<br>The opponent would answer: <i>{' '.join(result.reply_line_san)}</i> (red arrow)"
            try:
                reply = board.parse_san(result.reply_line_san[0])
                arrows.append(Arrow(reply.from_square, reply.to_square, style.PLAYED_ARROW))
            except ValueError:
                pass
        text += "<br>Press <b>Undo</b> (u) to try something else."
        self.board.set_position(board, last_move=chess.Move.from_uci(result.move_uci), arrows=arrows)
        self.retry_panel.set_feedback(text)

    def undo(self) -> None:
        if self.card is not None:
            self._attempt_token += 1
            self._set_up_board()
            self.retry_panel.set_feedback(
                self._answer_text() + "<br>Play it on the board to check it."
                if self.revealed_now else "Make your move on the board."
            )

    def hint(self) -> None:
        card = self.card
        if card is None or not card.mistake.best_move_uci:
            return
        if self.board.interactive_color is None:  # showing an attempt: rewind first
            self._attempt_token += 1
            self._set_up_board()
        self.review.hint()
        best = chess.Move.from_uci(card.mistake.best_move_uci)
        self.board.set_hint(best.from_square)
        self.retry_panel.set_feedback("Hint: the best move uses the piece on the highlighted square.")

    def reveal(self) -> None:
        card = self.card
        if card is None or not card.mistake.best_move_uci:
            return
        if not self.revealed_now:
            self.revealed_now = True
            self.review.reveal()
            try:
                self.db.record_reveal(card.mistake_id)
            except DatabaseError as exc:
                self.last_result.setText(f"⚠ Could not save the reveal: {exc}")
        self._attempt_token += 1
        self._set_up_board()
        self.retry_panel.set_feedback(self._answer_text() + "<br>Play it on the board to check it.")
        self._update_info()

    def _answer_arrows(self) -> list[Arrow]:
        card = self.card
        if card is None or not self.revealed_now or not card.mistake.best_move_uci:
            return []
        best = chess.Move.from_uci(card.mistake.best_move_uci)
        return [Arrow(best.from_square, best.to_square, style.BEST_ARROW)]

    def _answer_text(self) -> str:
        m = self.card.mistake
        text = f"The correct move was <b>{m.best_move_san}</b> (green arrow)."
        if m.best_line_san:
            text += f"<br>Engine line: <i>{' '.join(m.best_line_san)}</i>"
        return text + self._missed_text()

    def _missed_text(self) -> str:
        missed = [t.label for t in self.card.tags if t.spoiler]
        return f"<br>Pattern: <b>{', '.join(missed)}</b>." if missed else ""

    def _update_info(self) -> None:
        """The card's tags (no "missed" ones until solved or revealed) and a link to its game."""
        card = self.card
        if card is None:
            self.info.setText("")
            return
        open_now = self.solved_now or self.revealed_now
        tags = [t for t in card.tags if open_now or not t.spoiler]
        parts = []
        if tags:
            parts.append("Tags: " + ", ".join(t.label for t in tags))
        if open_now and card.has_pgn:
            parts.append(f"<a href='mistake:{card.mistake_id}'>Open the game</a>")
        self.info.setText("<br>".join(parts))

    def _link(self, href: str) -> None:
        if href.startswith("mistake:"):
            self.open_mistake.emit(int(href.split(":", 1)[1]))

    # --- keyboard and closing ---------------------------------------------

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        """The puzzle keys of the main window: hjkl + Space to move, u, H, s, n, q."""
        if event.type() != QEvent.Type.KeyPress:
            return False
        if not isinstance(obj, QWidget) or obj.window() is not self:
            return False
        if QApplication.activeModalWidget() not in (None, self):
            return False
        if isinstance(QApplication.focusWidget(), QLineEdit):
            return False  # typing on the ':' line: letters are just letters
        if event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        ):
            return False
        token = key_token(event)
        if token is None or not self.keys.handles(token, Mode.PUZZLE):
            self.keys.reset()
            return False
        command = self.keys.feed(token, Mode.PUZZLE)
        if command is not None:
            self.run_key_command(command.name, command.n)
        return True

    def run_key_command(self, name: str, n: int = 1) -> None:
        if name.startswith("cursor_"):
            right, up = {
                "cursor_left": (-1, 0), "cursor_right": (1, 0),
                "cursor_up": (0, 1), "cursor_down": (0, -1),
            }[name]
            self.board.move_cursor(right * n, up * n)
        elif name == "pick":
            self.board.press_cursor()
        elif name == "undo":
            if self.retry_panel.undo_button.isEnabled():
                self.undo()
        elif name == "hint":
            self.hint()
        elif name == "solution":
            self.reveal()
        elif name == "next_mistake":
            self.next_card()
        elif name == "done":
            self.finish()
        elif name == "flip":
            self.board.set_flipped(not self.board.flipped)
        elif name == "help":
            HelpDialog(self).exec()
        elif name == "cmdline":
            self.open_command_line()

    # --- the ':' command line -----------------------------------------------

    def open_command_line(self) -> None:
        self.message.clear()
        self.command_line.setText(":")
        self.command_line.show()
        self.command_line.setFocus()

    def close_command_line(self) -> None:
        self.board.set_preview_override(None)
        self.command_line.hide()
        self.command_line.clear()
        self.board.setFocus()

    def _preview_typed_move(self, text: str) -> None:
        """Show the blue preview arrow for a move while it's being typed."""
        if self.board.interactive_color is None:
            return
        self.board.set_preview_override(parse_command_line(text, self.board.board).move)

    def _run_command_line(self) -> None:
        text = self.command_line.text()
        self.close_command_line()
        playable = self.board.board if self.board.interactive_color is not None else None
        result = parse_command_line(text, playable)
        if result.error:
            extra = ""
            if playable is None:
                extra = " (press u to undo your last try first)" if self.card else ""
            self.message.setText(f"⚠ {result.error}{extra}")
        elif result.move is not None:
            self._attempt_made(result.move)
        elif result.command == "quit":
            self.finish()
        elif result.command == "help":
            HelpDialog(self).exec()
        elif result.command == "flip":
            self.board.set_flipped(not self.board.flipped)
        elif result.command:
            self.message.setText(
                f"⚠ :{text.strip().lstrip(':').strip()} works in the main window. "
                "Here: a move like :Nc4, or :flip, :help, :q."
            )

    def done(self, result: int) -> None:  # noqa: D401 - QDialog.done: closing by any route
        if not self.finished:
            self._leave_card()
            self.finished = True
        if self._attempt_thread and self._attempt_thread.isRunning():
            self._attempt_thread.wait()
        QApplication.instance().removeEventFilter(self)
        super().done(result)
