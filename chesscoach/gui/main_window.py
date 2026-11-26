"""Main window: load a game, analyze it, and browse your mistakes."""

from __future__ import annotations

from pathlib import Path

import chess
import chess.pgn
from PySide6.QtCore import QEvent, QSettings, QSize, Qt
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..analysis import GameAnalysis
from ..cache import AnalysisCache, analyze_with_cache
from ..classify import Judgement, MoveJudgement, flagged_moves, judge_game, player_color, side_accuracy
from ..db import DatabaseError, MistakeDB, parse_time_control, refutation_line
from ..engine import open_engine
from ..retry import AttemptResult, evaluate_attempt
from ..pgn import PGNError, read_game, read_game_file
from ..tags import Tag, tag_mistake
from .board import Arrow
from .dialogs import ChessComDialog, HelpDialog, PastePGNDialog, SideDialog, saved_username
from .evalgraph import EvalGraph
from .interactive import InteractiveBoard
from .keymap import Command, KeyParser, Mode, parse_command_line
from .retry_panel import RetryPanel
from .tasks import TaskThread
from . import style

SIDE_KEY = "player/last_side"

_SPECIAL_KEYS = {
    Qt.Key.Key_Escape: "Esc",
    Qt.Key.Key_Return: "Enter",
    Qt.Key.Key_Enter: "Enter",
    Qt.Key.Key_Space: "Space",
    Qt.Key.Key_Backspace: "Backspace",
    Qt.Key.Key_Left: "Left",
    Qt.Key.Key_Right: "Right",
    Qt.Key.Key_Up: "Up",
    Qt.Key.Key_Down: "Down",
    Qt.Key.Key_Home: "Home",
    Qt.Key.Key_End: "End",
}


def key_token(event) -> str | None:
    """Turn a Qt key event into a keymap token (see keymap.py)."""
    if event.key() in _SPECIAL_KEYS:
        return _SPECIAL_KEYS[event.key()]
    text = event.text()
    return text if len(text) == 1 and text.isprintable() else None


class CommandLine(QLineEdit):
    """Vim-style ':' line at the bottom of the window."""

    def __init__(self, on_cancel, parent=None) -> None:
        super().__init__(parent)
        self._on_cancel = on_cancel
        # Deleting the leading ':' closes the line, like vim.
        self.textEdited.connect(lambda text: None if text.startswith(":") else on_cancel())

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(event)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # QLineEdit lets Enter bubble up to the window, where it means "retry".
            event.accept()

FILTERS = [
    ("Inaccuracies, mistakes and blunders", Judgement.INACCURACY),
    ("Mistakes and blunders", Judgement.MISTAKE),
    ("Blunders only", Judgement.BLUNDER),
]


class RoomyRows(QStyledItemDelegate):
    """List rows a little taller than Qt's guess.

    Mistake rows have two lines (move, then tags); Qt sizes them by the
    normal font, so a row starting with an emoji mark (taller fallback font)
    would get its second line cut off.
    """

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        size = super().sizeHint(option, index)
        return QSize(size.width(), size.height() + 6)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("chessCoach")
        self.resize(1200, 780)
        self.setAcceptDrops(True)

        self.game: chess.pgn.Game | None = None
        self.boards: list[chess.Board] = []  # boards[ply] = position after `ply` moves
        self.moves: list[chess.Move] = []
        self.analysis: GameAnalysis | None = None
        self.judgements: list[MoveJudgement] = []
        self.my_color: chess.Color | None = None
        self.current_ply = 0
        self._thread: TaskThread | None = None
        # Retry mode: the ply of the mistake being retried, and which ones were fixed.
        self.retry_ply: int | None = None
        self.solved: set[int] = set()
        # Mistakes whose answer the player chose to reveal. Until then the best
        # move is hidden everywhere so the player has to find it themselves.
        self.revealed: set[int] = set()
        self._attempt_thread: TaskThread | None = None
        self._attempt_token = 0
        # Your mistakes are saved to the mistake database; ply -> mistakes.id.
        self.db = MistakeDB()
        self.mistake_ids: dict[int, int] = {}
        # Pattern tags of every flagged move; ply -> tags. "Missed" tags give
        # the answer away, so for your mistakes they stay hidden until solved
        # or revealed (see _visible_tags).
        self.tags: dict[int, list[Tag]] = {}

        self._build_actions()
        self._build_layout()
        self._show_placeholder()

    # --- construction ---------------------------------------------------

    def _build_actions(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        def action(text: str, slot, keys: str, shortcut=None, on_toolbar=True) -> QAction:
            act = QAction(text, self)
            act.triggered.connect(slot)
            act.setToolTip(f"{text.rstrip('…')}  ({keys})")
            if shortcut:
                act.setShortcut(shortcut)
            if on_toolbar:
                toolbar.addAction(act)
            else:
                self.addAction(act)
            return act

        # Single-letter keys are handled by the vim key parser (see keymap.py),
        # not by QAction shortcuts, so they can't fire while you type in a text box.
        action("Open PGN…", self.open_file, "Ctrl+O or :e", QKeySequence.StandardKey.Open)
        action("Paste PGN…", self.paste_pgn, "Ctrl+Shift+V or :paste", QKeySequence("Ctrl+Shift+V"))
        action("Chess.com…", self.import_chesscom, "Ctrl+I or :cc", QKeySequence("Ctrl+I"))
        toolbar.addSeparator()
        action("Flip board", self.flip_board, "f")
        self.retry_action = action("Try a better move", lambda: self.start_retry(), "t or Enter")
        self.retry_action.setEnabled(False)
        toolbar.addSeparator()
        action("Keys", self.show_help, "?")
        action("Quit", self.close, "Ctrl+Q or :q", QKeySequence("Ctrl+Q"), on_toolbar=False)

    def _build_layout(self) -> None:
        # Left: board, navigation, graph.
        self.board = InteractiveBoard()
        self.board.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.board.move_made.connect(self._attempt_made)
        nav = QHBoxLayout()
        for text, slot, tip in [
            ("⏮", lambda: self.go_to(0), "Start (gg / Home)"),
            ("◀", lambda: self.go_to(self.current_ply - 1), "Previous move (h / ←)"),
            ("▶", lambda: self.go_to(self.current_ply + 1), "Next move (l / →)"),
            ("⏭", lambda: self.go_to(len(self.moves)), "End (G / End)"),
        ]:
            button = QPushButton(text)
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(slot)
            nav.addWidget(button)
        self.graph = EvalGraph()
        self.graph.ply_clicked.connect(self.go_to)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.board, 1)
        left_layout.addLayout(nav)
        left_layout.addWidget(self.graph)

        # Right: summary, mistakes list, move details, move table.
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)

        self.filter_box = QComboBox()
        self.filter_box.setToolTip("Which mistakes to list (c cycles)")
        for label, _ in FILTERS:
            self.filter_box.addItem(label)
        self.filter_box.setCurrentIndex(1)
        self.filter_box.currentIndexChanged.connect(self._populate_mistakes)
        self.filter_box.currentIndexChanged.connect(lambda _i: self._update_next_button())

        self.mistakes = QListWidget()
        self.mistakes.setItemDelegate(RoomyRows(self.mistakes))
        self.mistakes.setToolTip("n / N: next / previous mistake")
        self.mistakes.currentItemChanged.connect(self._mistake_selected)
        self.mistakes.itemDoubleClicked.connect(lambda _item: self.start_retry())

        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setMinimumHeight(70)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.retry_button = QPushButton("Try a better move  (t)")
        self.retry_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.retry_button.setToolTip("Go back to the position before this move and find a better one")
        self.retry_button.clicked.connect(lambda: self.start_retry())
        self.retry_button.hide()

        self.retry_panel = RetryPanel()
        self.retry_panel.hint_requested.connect(self._retry_hint)
        self.retry_panel.answer_requested.connect(self._retry_answer)
        self.retry_panel.undo_requested.connect(self._retry_undo)
        self.retry_panel.next_requested.connect(lambda: self.step_mistake(1))
        self.retry_panel.exit_requested.connect(self.exit_retry)
        self.retry_panel.hide()

        self.move_table = QTableWidget(0, 2)
        self.move_table.setHorizontalHeaderLabels(["White", "Black"])
        self.move_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.move_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.move_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.move_table.cellClicked.connect(lambda row, col: self.go_to(row * 2 + col + 1))

        right = QWidget()
        self.right_layout = QVBoxLayout(right)
        self.right_layout.addWidget(self.summary)
        self.right_layout.addWidget(QLabel("<b>Your mistakes</b>"))
        self.right_layout.addWidget(self.filter_box)
        self.right_layout.addWidget(self.mistakes, 2)
        self.right_layout.addWidget(self.details)
        self.right_layout.addWidget(self.retry_button)
        self.right_layout.addWidget(self.retry_panel)
        self.right_layout.addWidget(self.move_table, 3)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.setCentralWidget(splitter)

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.hide()
        self.cancel_button = QPushButton("Cancel  (Esc)")
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cancel_button.clicked.connect(self.cancel_analysis)
        self.cancel_button.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_button)

        # Plain-language readout of the cursor / previewed move ("Knight g1 → f3 (Nf3)"),
        # to help with learning square names.
        self.square_label = QLabel()
        self.statusBar().addPermanentWidget(self.square_label)
        self.board.status_changed.connect(self.square_label.setText)

        # Vim-ish bottom line: mode + pending keys, and the ':' command line.
        self.mode_label = QLabel()
        self.statusBar().addPermanentWidget(self.mode_label)  # stays visible during messages
        self.command_line = CommandLine(self.close_command_line)
        self.command_line.returnPressed.connect(self._run_command_line)
        self.command_line.textEdited.connect(self._preview_typed_move)
        self.command_line.hide()
        self.statusBar().addWidget(self.command_line, 1)

        self.keys = KeyParser()
        QApplication.instance().installEventFilter(self)
        self._update_mode_label()

    def _show_placeholder(self) -> None:
        self.summary.setText(
            "<h3>Load a game to start</h3>"
            "Open a <code>.pgn</code> file (Ctrl+O), paste PGN text (Ctrl+Shift+V), "
            "import from Chess.com (Ctrl+I), or drop a .pgn file on this window."
            "<br>Press <b>?</b> for all keyboard shortcuts."
        )

    # --- loading games --------------------------------------------------

    def open_file(self) -> None:
        start_dir = str(QSettings().value("files/last_dir", str(Path.home())))
        path, _ = QFileDialog.getOpenFileName(self, "Open PGN", start_dir, "PGN files (*.pgn);;All files (*)")
        if path:
            QSettings().setValue("files/last_dir", str(Path(path).parent))
            self.load_file(path)

    def load_file(self, path: str) -> None:
        try:
            game = read_game_file(path)
        except (PGNError, OSError) as exc:
            QMessageBox.warning(self, "Could not open game", str(exc))
            return
        self.load_game(game, Path(path).name)

    def paste_pgn(self) -> None:
        dialog = PastePGNDialog(self)
        if dialog.exec():
            try:
                game = read_game(dialog.pgn_text())
            except PGNError as exc:
                QMessageBox.warning(self, "Could not read PGN", str(exc))
                return
            self.load_game(game, "pasted game")

    def import_chesscom(self) -> None:
        dialog = ChessComDialog(self)
        if dialog.exec() and dialog.selected:
            try:
                game = read_game(dialog.selected.pgn)
            except PGNError as exc:
                QMessageBox.warning(self, "Could not read game", str(exc))
                return
            self.load_game(game, dialog.selected.url)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasUrls() and mime.urls()[0].isLocalFile():
            self.load_file(mime.urls()[0].toLocalFile())
        elif mime.hasText():
            try:
                self.load_game(read_game(mime.text()), "dropped text")
            except PGNError as exc:
                QMessageBox.warning(self, "Could not read PGN", str(exc))

    def load_game(self, game: chess.pgn.Game, source: str) -> None:
        self.exit_retry(navigate=False)
        self.solved = set()
        self.revealed = set()
        self.mistake_ids = {}
        self.tags = {}
        self.cancel_analysis()
        self.game = game
        self.analysis = None
        self.judgements = []
        self.my_color = None
        board = game.board()
        self.boards = [board.copy()]
        self.moves = []
        for move in game.mainline_moves():
            board.push(move)
            self.moves.append(move)
            self.boards.append(board.copy())

        h = game.headers
        self.setWindowTitle(f"chessCoach — {h.get('White', '?')} vs {h.get('Black', '?')}")
        self.summary.setText(
            f"<h3>{h.get('White', '?')} vs {h.get('Black', '?')}</h3>"
            f"{h.get('Date', '')} · {h.get('Result', '')} · <i>{source}</i><br>Analyzing…"
        )
        self.mistakes.clear()
        self.details.clear()
        self.graph.set_judgements([])
        self._populate_move_table()
        self.go_to(0)
        self._start_analysis(game)

    # --- analysis -------------------------------------------------------

    def _start_analysis(self, game: chess.pgn.Game) -> None:
        def work(progress, should_stop):
            return analyze_with_cache(
                game, cache=AnalysisCache(), progress=progress, should_stop=should_stop
            )

        self.progress.setValue(0)
        self.progress.show()
        self.cancel_button.show()
        self.statusBar().showMessage("Analyzing with Stockfish…")
        thread = TaskThread(work, self)
        thread.progress.connect(self._on_progress)
        thread.succeeded.connect(self._analysis_done)
        thread.failed.connect(self._analysis_failed)
        thread.finished.connect(self._analysis_finished)
        self._thread = thread
        thread.start()

    def cancel_analysis(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.requestInterruption()
            self._thread.wait()
            self.statusBar().showMessage("Analysis canceled", 4000)
        self._thread = None
        self._analysis_finished()

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(done)

    def _analysis_finished(self) -> None:
        self.progress.hide()
        self.cancel_button.hide()

    def _analysis_failed(self, message: str) -> None:
        self.statusBar().clearMessage()
        QMessageBox.critical(self, "Analysis failed", message)

    def _analysis_done(self, analysis: GameAnalysis) -> None:
        self.analysis = analysis
        self.judgements = judge_game(analysis)
        self.tags = self._compute_tags(analysis)
        self.my_color = self._ask_my_color(analysis.headers)
        self.statusBar().showMessage(f"Analysis complete{self._save_mistakes()}", 6000)
        if self.my_color is not None:
            self.board.set_flipped(self.my_color == chess.BLACK)
        self.graph.set_judgements(self.judgements)
        self._populate_summary()
        self._populate_move_table()
        self._populate_mistakes()
        self.go_to(self.current_ply)

    def _compute_tags(self, analysis: GameAnalysis) -> dict[int, list[Tag]]:
        base_seconds, _ = parse_time_control(analysis.headers.get("TimeControl"))
        return {
            j.move.ply: tag_mistake(j.move, refutation_line(analysis, j.move.ply), base_seconds)
            for j in self.judgements
            if j.judgement.is_error
        }

    def _visible_tags(self, j: MoveJudgement) -> list[Tag]:
        """Tags that don't give the answer away: for your unsolved mistakes, no "missed" tags."""
        ply = j.move.ply
        tags = self.tags.get(ply, [])
        if self._is_my_error(j) and ply not in self.solved and ply not in self.revealed:
            return [t for t in tags if not t.spoiler]
        return tags

    def _missed_text(self, ply: int | None) -> str:
        """The "missed" tags as a line of feedback, once the answer may be shown."""
        missed = [t.label for t in self.tags.get(ply, []) if t.spoiler]
        return f"<br>Pattern: <b>{', '.join(missed)}</b>." if missed else ""

    def _save_mistakes(self) -> str:
        """Store your mistakes in the mistake database; returns a note for the status bar."""
        if self.analysis is None or self.my_color is None:
            return ""
        try:
            recorded = self.db.record_game(self.analysis, self.judgements, self.my_color)
        except DatabaseError as exc:
            return f" · could not save your mistakes: {exc}"
        self.mistake_ids = recorded.mistake_ids
        if recorded.added:
            n = recorded.added
            return f" · saved {n} new mistake{'s' if n != 1 else ''} to your mistake database"
        if recorded.mistake_ids:
            return " · this game's mistakes are already in your mistake database"
        return ""

    def _log_retry(self, record, *args) -> None:
        """Log a retry event for the mistake being retried; never interrupts the puzzle."""
        mistake_id = self.mistake_ids.get(self.retry_ply)
        if mistake_id is None:
            return
        try:
            record(mistake_id, *args)
        except DatabaseError as exc:
            self.statusBar().showMessage(f"Could not save the retry: {exc}", 6000)

    def _ask_my_color(self, headers: dict[str, str]) -> chess.Color | None:
        """Work out which side the user played, asking if the names don't say."""
        color = player_color(headers, saved_username())
        if color is not None:
            return color
        last = QSettings().value(SIDE_KEY, "white")
        box = SideDialog(headers.get("White", "?"), headers.get("Black", "?"), last == "black", self)
        box.exec()
        if box.clickedButton() is box.white:
            QSettings().setValue(SIDE_KEY, "white")
            return chess.WHITE
        if box.clickedButton() is box.black:
            QSettings().setValue(SIDE_KEY, "black")
            return chess.BLACK
        return None

    # --- populating views -----------------------------------------------

    def _populate_summary(self) -> None:
        assert self.analysis is not None
        h = self.analysis.headers
        lines = [f"<h3>{h.get('White', '?')} vs {h.get('Black', '?')}</h3>",
                 f"{h.get('Date', '')} · {h.get('Result', '')} · {h.get('Termination', '')}"]
        for color, name in ((chess.WHITE, "White"), (chess.BLACK, "Black")):
            acc = side_accuracy(self.judgements, color)
            counts = {j: 0 for j in (Judgement.INACCURACY, Judgement.MISTAKE, Judgement.BLUNDER)}
            for jm in self.judgements:
                if jm.move.mover == color and jm.judgement in counts:
                    counts[jm.judgement] += 1
            you = " <b>(you)</b>" if color == self.my_color else ""
            acc_text = f"{acc:.0f}%" if acc is not None else "–"
            lines.append(
                f"<br>{name}{you}: accuracy {acc_text} · "
                f"{counts[Judgement.INACCURACY]} ?! · {counts[Judgement.MISTAKE]} ? · "
                f"{counts[Judgement.BLUNDER]} ??"
            )
        self.summary.setText("".join(lines))

    def _populate_move_table(self) -> None:
        self.move_table.setRowCount((len(self.moves) + 1) // 2)
        self.move_table.setVerticalHeaderLabels([str(i + 1) for i in range(self.move_table.rowCount())])
        for ply in range(1, len(self.moves) + 1):
            board = self.boards[ply - 1]
            text = board.san(self.moves[ply - 1])
            item = QTableWidgetItem(text)
            if self.judgements:
                j = self.judgements[ply - 1]
                item.setText(text + j.judgement.symbol)
                color = style.judgement_color(j.judgement)
                if color is not None:
                    item.setForeground(QBrush(color))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
            self.move_table.setItem((ply - 1) // 2, (ply - 1) % 2, item)

    def _populate_mistakes(self) -> None:
        self.mistakes.blockSignals(True)
        self.mistakes.clear()
        minimum = FILTERS[self.filter_box.currentIndex()][1]
        for j in flagged_moves(self.judgements, self.my_color, minimum):
            m = j.move
            mark = "✓ " if m.ply in self.solved else "👁 " if m.ply in self.revealed else ""
            text = f"{mark}{m.label}{j.judgement.symbol}  {j.judgement.value} (−{j.win_loss:.0f}%)"
            if m.ply in self.revealed:
                text += f" · best {m.best_move_san}"
            tags = self._visible_tags(j)
            if tags:
                text += "\n      " + " · ".join(t.label for t in tags)
            item = QListWidgetItem(text)
            item.setForeground(QBrush(style.judgement_color(j.judgement)))
            item.setData(Qt.ItemDataRole.UserRole, m.ply)
            self.mistakes.addItem(item)
        if self.mistakes.count() == 0 and self.judgements:
            self.mistakes.addItem("No mistakes at this level. Nice game!")
        self._select_mistake_item(self.retry_ply)
        self.mistakes.blockSignals(False)

    def _select_mistake_item(self, ply: int | None) -> None:
        """Highlight the list row for ``ply`` without triggering navigation."""
        blocked = self.mistakes.blockSignals(True)
        self.mistakes.setCurrentRow(-1)
        for row in range(self.mistakes.count()):
            if self.mistakes.item(row).data(Qt.ItemDataRole.UserRole) == ply:
                self.mistakes.setCurrentRow(row)
                break
        self.mistakes.blockSignals(blocked)

    def _mistake_selected(self, item: QListWidgetItem | None, _previous=None) -> None:
        # Picking one of your mistakes goes straight to the puzzle: find a better move.
        if item is not None and item.data(Qt.ItemDataRole.UserRole):
            self.start_retry(int(item.data(Qt.ItemDataRole.UserRole)))

    def _answer_hidden(self, j: MoveJudgement | None) -> bool:
        """True while the best move for one of your mistakes is still a secret."""
        return self._is_my_error(j) and j.move.ply not in self.revealed

    # --- navigation -----------------------------------------------------

    def go_to(self, ply: int) -> None:
        if not self.boards:
            return
        if self.retry_ply is not None:
            self.exit_retry(navigate=False)
        ply = max(0, min(len(self.moves), ply))
        self.current_ply = ply
        last_move = self.moves[ply - 1] if ply > 0 else None
        judgement = self.judgements[ply - 1] if ply > 0 and self.judgements else None

        arrows: list[Arrow] = []
        highlight = None
        if judgement is not None and judgement.judgement.is_error:
            highlight = QColor(style.judgement_color(judgement.judgement))
            highlight.setAlpha(150)
            if judgement.move.best_move_uci and not self._answer_hidden(judgement):
                best = chess.Move.from_uci(judgement.move.best_move_uci)
                arrows.append(Arrow(best.from_square, best.to_square, style.BEST_ARROW))

        self.board.set_position(self.boards[ply], last_move=last_move,
                                highlight_color=highlight, arrows=arrows)
        self.graph.set_current_ply(ply)
        self._show_details(judgement)
        can_retry = self._is_my_error(judgement)
        self.retry_button.setVisible(can_retry)
        self.retry_action.setEnabled(can_retry)
        if ply > 0:
            self.move_table.setCurrentCell((ply - 1) // 2, (ply - 1) % 2)
        else:
            self.move_table.clearSelection()

    def _show_details(self, j: MoveJudgement | None) -> None:
        if j is None:
            self.details.setText("")
            return
        m = j.move
        text = f"<b>{m.label}</b> — eval {m.eval_after}"
        if j.judgement is Judgement.BEST:
            text += " · <span style='color:#2e8b37'>best move</span>"
        elif j.judgement.is_error:
            color = style.judgement_color(j.judgement).name()
            text += (
                f"<br><span style='color:{color}'><b>{j.judgement.value.capitalize()}</b></span>: "
                f"win chance {j.win_before:.0f}% → {j.win_after:.0f}%."
            )
            if self._answer_hidden(j):
                text += "<br>There was a better move. Can you find it? Press <b>T</b> to try."
            else:
                text += f"<br>Best was <b>{m.best_move_san}</b> (green arrow)"
                if m.best_line_san:
                    text += f": <i>{' '.join(m.best_line_san)}</i>"
            tags = self._visible_tags(j)
            if tags:
                text += "<br>Tags: " + ", ".join(t.label for t in tags)
        self.details.setText(text)

    # --- retry mode -----------------------------------------------------

    def _is_my_error(self, j: MoveJudgement | None) -> bool:
        return (
            j is not None
            and j.judgement.is_error
            and (self.my_color is None or j.move.mover == self.my_color)
        )

    def _judgement_at(self, ply: int | None) -> MoveJudgement | None:
        if ply is None or not (1 <= ply <= len(self.judgements)):
            return None
        return self.judgements[ply - 1]

    def start_retry(self, ply: int | None = None) -> None:
        """Rewind to just before the mistake at ``ply`` and let the player try again."""
        ply = self.current_ply if ply is None else ply
        j = self._judgement_at(ply)
        if not self._is_my_error(j):
            return
        self._attempt_token += 1  # ignore results from any earlier attempt
        self.retry_ply = ply
        self.current_ply = ply
        m = j.move
        before = self.boards[ply - 1]
        previous = self.moves[ply - 2] if ply >= 2 else None
        self.board.set_flipped(m.mover == chess.BLACK)
        self.board.set_position(before, last_move=previous, arrows=self._answer_arrows(j))
        self.board.set_marks(self._original_move_marks(j))
        self.board.set_interactive(m.mover)
        self._select_mistake_item(ply)
        self.graph.set_current_ply(ply - 1)

        side = "White" if m.mover == chess.WHITE else "Black"
        color = style.judgement_color(j.judgement).name()
        self.retry_panel.set_prompt(
            f"<h3>Find a better move for {side}</h3>"
            f"In the game you played <b style='color:{color}'>{m.san}{j.judgement.symbol}</b> "
            f"({j.judgement.value}, win chance {j.win_before:.0f}% → {j.win_after:.0f}%). "
            f"It stays marked in <span style='color:{color}'>{style.judgement_color_name(j.judgement)}</span> "
            "on the board."
        )
        if ply in self.revealed:
            self.retry_panel.set_feedback(self._answer_text(j) + "<br>Play it on the board to check it.")
        else:
            self.retry_panel.set_feedback("Make your move on the board.")
        self.retry_panel.set_undo_enabled(False)
        self._update_next_button()
        self.keys.reset()
        self._update_mode_label()
        self.board.setFocus()
        self.details.hide()
        self.retry_button.hide()
        self.retry_action.setEnabled(False)
        self.retry_panel.show()

    def _original_move_marks(self, j: MoveJudgement) -> list:
        """Tint the from/to squares of the move you actually played, in its severity color."""
        color = QColor(style.judgement_color(j.judgement))
        color.setAlpha(150)
        move = chess.Move.from_uci(j.move.uci)
        return [(move.from_square, color), (move.to_square, color)]

    def exit_retry(self, navigate: bool = True) -> None:
        if self.retry_ply is None:
            return
        ply = self.retry_ply
        self.retry_ply = None
        self._attempt_token += 1
        self.board.set_interactive(None)
        self.board.set_marks([])
        self.retry_panel.hide()
        self.details.show()
        self.keys.reset()
        self._update_mode_label()
        if navigate:
            self.go_to(ply)

    def _attempt_made(self, move: chess.Move) -> None:
        j = self._judgement_at(self.retry_ply)
        if j is None:
            return
        board = self.boards[self.retry_ply - 1].copy()
        san = board.san(move)
        board.push(move)
        self.board.set_interactive(None)
        self.board.set_hint(None)
        self.board.set_position(board, last_move=move)
        self.retry_panel.set_feedback(f"Checking <b>{san}</b>…")
        self.retry_panel.set_undo_enabled(True)

        self._attempt_token += 1
        token = self._attempt_token
        mistake = j.move

        def work(_progress, _stop):
            with open_engine() as engine:
                return evaluate_attempt(engine, mistake, move)

        thread = TaskThread(work, self)
        thread.succeeded.connect(lambda result: self._attempt_judged(token, board, result))
        thread.failed.connect(lambda msg: self.retry_panel.set_feedback(f"⚠ {msg}"))
        self._attempt_thread = thread
        thread.start()

    def _attempt_judged(self, token: int, board: chess.Board, result: AttemptResult) -> None:
        if token != self._attempt_token or self.retry_ply is None:
            return
        self._log_retry(self.db.record_attempt, result)
        if result.solved:
            self.solved.add(self.retry_ply)
            self._populate_mistakes()
            text = f"<span style='color:#2e8b37'><b>✓ {result.feedback()}</b></span>"
            text += self._missed_text(self.retry_ply)
            if result.judgement is not Judgement.BEST and self.retry_ply not in self.revealed:
                text += "<br>The engine liked another move a little more. <b>Undo move</b> to look for it."
            remaining = self._unsolved_plies()
            if remaining:
                left = "1 more" if len(remaining) == 1 else f"{len(remaining)} more"
                text += f"<br>Press <b>Next mistake</b> (n) to keep going: {left} to fix."
            else:
                text += "<br><b>🎉 That was the last one. You've fixed every mistake in this list.</b>"
                if self.filter_box.currentIndex() > 0:
                    text += "<br>Press <b>c</b> to include smaller mistakes, or <b>Done</b> (q) to finish."
                else:
                    text += "<br>Press <b>Done</b> (q) to finish."
            self.retry_panel.set_feedback(text)
            self._update_next_button()
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
        text += "<br>Press <b>Undo move</b> to try something else."
        self.board.set_position(board, last_move=chess.Move.from_uci(result.move_uci), arrows=arrows)
        self.retry_panel.set_feedback(text)

    def _retry_hint(self) -> None:
        j = self._judgement_at(self.retry_ply)
        if j is None or not j.move.best_move_uci:
            return
        if self.board.interactive_color is None:  # showing a previous attempt: rewind first
            self.start_retry(self.retry_ply)
        best = chess.Move.from_uci(j.move.best_move_uci)
        self.board.set_hint(best.from_square)
        self.retry_panel.set_feedback("Hint: the best move uses the piece on the highlighted square.")

    def _answer_arrows(self, j: MoveJudgement) -> list[Arrow]:
        if j.move.ply not in self.revealed or not j.move.best_move_uci:
            return []
        best = chess.Move.from_uci(j.move.best_move_uci)
        return [Arrow(best.from_square, best.to_square, style.BEST_ARROW)]

    def _answer_text(self, j: MoveJudgement) -> str:
        m = j.move
        text = f"The correct move was <b>{m.best_move_san}</b> (green arrow)."
        if m.best_line_san:
            text += f"<br>Engine line: <i>{' '.join(m.best_line_san)}</i>"
        text += self._missed_text(m.ply)
        return text

    def _retry_undo(self) -> None:
        if self.retry_ply is not None:
            self.start_retry(self.retry_ply)

    def _retry_answer(self) -> None:
        j = self._judgement_at(self.retry_ply)
        if j is None or not j.move.best_move_uci:
            return
        if self.retry_ply not in self.revealed:
            self._log_retry(self.db.record_reveal)
        self.revealed.add(self.retry_ply)
        self._populate_mistakes()
        self.start_retry(self.retry_ply)

    def _unsolved_plies(self) -> list[int]:
        """Your mistakes in the current filter that haven't been solved yet."""
        minimum = FILTERS[self.filter_box.currentIndex()][1]
        return [
            j.move.ply
            for j in flagged_moves(self.judgements, self.my_color, minimum)
            if j.move.ply not in self.solved
        ]

    def _update_next_button(self) -> None:
        """'Next mistake' only makes sense while another unsolved mistake exists."""
        others = [p for p in self._unsolved_plies() if p != self.retry_ply]
        self.retry_panel.next_button.setEnabled(bool(others))

    def step_mistake(self, direction: int) -> None:
        """Start the puzzle for the next (1) or previous (-1) unsolved mistake, wrapping around."""
        minimum = FILTERS[self.filter_box.currentIndex()][1]
        plies = [j.move.ply for j in flagged_moves(self.judgements, self.my_color, minimum)]
        unsolved = [p for p in plies if p not in self.solved]
        if not plies:
            self.statusBar().showMessage("No mistakes in this list.", 3000)
            return
        if not unsolved:
            if self.retry_ply is not None:
                self.retry_panel.set_feedback("<b>You've fixed every mistake in this list. 🎉</b>")
            else:
                self.statusBar().showMessage("You've fixed every mistake in this list.", 4000)
            return
        current = self.retry_ply if self.retry_ply is not None else self.current_ply
        if direction > 0:
            later = [p for p in unsolved if p > current]
            self.start_retry(later[0] if later else unsolved[0])
        else:
            earlier = [p for p in unsolved if p < current]
            self.start_retry(earlier[-1] if earlier else unsolved[-1])

    def cycle_filter(self) -> None:
        self.filter_box.setCurrentIndex((self.filter_box.currentIndex() + 1) % self.filter_box.count())
        self.statusBar().showMessage(f"Showing: {self.filter_box.currentText()}", 2500)

    def show_help(self) -> None:
        HelpDialog(self).exec()

    def flip_board(self) -> None:
        self.board.set_flipped(not self.board.flipped)

    # --- keyboard ---------------------------------------------------------

    @property
    def mode(self) -> Mode:
        return Mode.PUZZLE if self.retry_ply is not None else Mode.NORMAL

    def _update_mode_label(self) -> None:
        name = "-- PUZZLE --" if self.mode is Mode.PUZZLE else "-- NORMAL --"
        self.mode_label.setText(f"<b>{name}</b>  {self.keys.pending}")

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        """Route key presses anywhere in this window through the vim key parser."""
        if event.type() != QEvent.Type.KeyPress:
            return False
        if not isinstance(obj, QWidget) or obj.window() is not self:
            return False
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return False
        if isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit, QAbstractSpinBox)):
            return False  # typing in a text box: letters are just letters
        if event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        ):
            return False  # leave Ctrl+… to the QAction shortcuts
        token = key_token(event)
        if token is None or not self.keys.handles(token, self.mode):
            if token is not None and self.keys.pending:
                self.keys.reset()  # an unknown key cancels a half-typed command
                self._update_mode_label()
            return False
        command = self.keys.feed(token, self.mode)
        if command is not None:
            self.run_key_command(command)
        self._update_mode_label()
        return True

    def run_key_command(self, cmd: Command) -> None:
        n, name = cmd.n, cmd.name
        if name == "prev":
            self.go_to(self.current_ply - n)
        elif name == "next":
            self.go_to(self.current_ply + n)
        elif name == "up":
            self.go_to(self.current_ply - 2 * n)
        elif name == "down":
            self.go_to(self.current_ply + 2 * n)
        elif name in ("start", "end"):
            if cmd.count is not None:
                self.go_to(2 * cmd.count - 1)  # after White's move number N
            else:
                self.go_to(0 if name == "start" else len(self.moves))
        elif name == "retry":
            if self._is_my_error(self._judgement_at(self.current_ply)):
                self.start_retry()
            else:
                self.statusBar().showMessage("Not one of your mistakes. Press n for the next one.", 3000)
        elif name == "cancel":
            if self._thread and self._thread.isRunning():
                self.cancel_analysis()
        elif name == "flip":
            self.flip_board()
        elif name == "help":
            self.show_help()
        elif name == "cmdline":
            self.open_command_line()
        elif name == "next_mistake":
            self.step_mistake(1)
        elif name == "prev_mistake":
            self.step_mistake(-1)
        elif name == "cycle_filter":
            self.cycle_filter()
        elif name.startswith("cursor_"):
            right, up = {
                "cursor_left": (-1, 0), "cursor_right": (1, 0),
                "cursor_up": (0, 1), "cursor_down": (0, -1),
            }[name]
            self.board.move_cursor(right * n, up * n)
        elif name == "pick":
            self.board.press_cursor()
        elif name == "undo":
            if self.retry_panel.undo_button.isEnabled():
                self._retry_undo()
        elif name == "hint":
            self._retry_hint()
        elif name == "solution":
            self._retry_answer()
        elif name == "done":
            self.exit_retry()

    def open_command_line(self) -> None:
        self.statusBar().clearMessage()  # a temporary message would hide the line
        self.command_line.setText(":")
        self.command_line.show()
        self.command_line.setFocus()

    def _preview_typed_move(self, text: str) -> None:
        """Show the blue preview arrow for a move while it's being typed."""
        if self.board.interactive_color is None:
            return
        self.board.set_preview_override(parse_command_line(text, self.board.board).move)

    def close_command_line(self) -> None:
        self.board.set_preview_override(None)
        self.command_line.hide()
        self.command_line.clear()
        self.board.setFocus()

    def _run_command_line(self) -> None:
        text = self.command_line.text()
        self.close_command_line()
        playable = self.board.board if self.board.interactive_color is not None else None
        result = parse_command_line(text, playable)
        if result.error:
            extra = "" if playable is not None else " (moves can be typed while solving a puzzle)"
            self.statusBar().showMessage(result.error + extra, 4000)
        elif result.move is not None:
            self._attempt_made(result.move)
        elif result.command:
            {
                "open": self.open_file,
                "paste": self.paste_pgn,
                "chesscom": self.import_chesscom,
                "quit": self.close,
                "help": self.show_help,
                "flip": self.flip_board,
            }[result.command]()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.cancel_analysis()
        if self._attempt_thread and self._attempt_thread.isRunning():
            self._attempt_thread.wait()
        super().closeEvent(event)
