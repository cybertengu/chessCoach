"""Main window: load a game, analyse it, and browse your mistakes."""

from __future__ import annotations

from pathlib import Path

import chess
import chess.pgn
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..analysis import GameAnalysis, analyze_game
from ..classify import Judgement, MoveJudgement, flagged_moves, judge_game, player_color, side_accuracy
from ..engine import open_engine
from ..retry import AttemptResult, evaluate_attempt
from ..pgn import PGNError, read_game, read_game_file
from .board import Arrow
from .dialogs import ChessComDialog, PastePGNDialog, saved_username
from .evalgraph import EvalGraph
from .interactive import InteractiveBoard
from .retry_panel import RetryPanel
from .tasks import TaskThread
from . import style

SIDE_KEY = "player/last_side"

FILTERS = [
    ("Inaccuracies, mistakes and blunders", Judgement.INACCURACY),
    ("Mistakes and blunders", Judgement.MISTAKE),
    ("Blunders only", Judgement.BLUNDER),
]


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

        self._build_actions()
        self._build_layout()
        self._show_placeholder()

    # --- construction ---------------------------------------------------

    def _build_actions(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        def action(text: str, slot, shortcut=None) -> QAction:
            act = QAction(text, self)
            act.triggered.connect(slot)
            if shortcut:
                act.setShortcut(shortcut)
            toolbar.addAction(act)
            return act

        action("Open PGN…", self.open_file, QKeySequence.StandardKey.Open)
        action("Paste PGN…", self.paste_pgn, QKeySequence("Ctrl+Shift+V"))
        action("Chess.com…", self.import_chesscom, QKeySequence("Ctrl+I"))
        toolbar.addSeparator()
        action("Flip board", self.flip_board, QKeySequence("F"))
        self.retry_action = action("Try a better move", lambda: self.start_retry(), QKeySequence("T"))
        self.retry_action.setEnabled(False)

    def _build_layout(self) -> None:
        # Left: board, navigation, graph.
        self.board = InteractiveBoard()
        self.board.move_made.connect(self._attempt_made)
        nav = QHBoxLayout()
        for text, slot, tip in [
            ("⏮", lambda: self.go_to(0), "Start (Home)"),
            ("◀", lambda: self.go_to(self.current_ply - 1), "Previous move (←)"),
            ("▶", lambda: self.go_to(self.current_ply + 1), "Next move (→)"),
            ("⏭", lambda: self.go_to(len(self.moves)), "End (End)"),
        ]:
            button = QPushButton(text)
            button.setToolTip(tip)
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
        for label, _ in FILTERS:
            self.filter_box.addItem(label)
        self.filter_box.setCurrentIndex(1)
        self.filter_box.currentIndexChanged.connect(self._populate_mistakes)

        self.mistakes = QListWidget()
        self.mistakes.currentItemChanged.connect(self._mistake_selected)
        self.mistakes.itemDoubleClicked.connect(lambda _item: self.start_retry())

        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setMinimumHeight(70)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.retry_button = QPushButton("Try a better move  (T)")
        self.retry_button.setToolTip("Go back to the position before this move and find a better one")
        self.retry_button.clicked.connect(lambda: self.start_retry())
        self.retry_button.hide()

        self.retry_panel = RetryPanel()
        self.retry_panel.hint_requested.connect(self._retry_hint)
        self.retry_panel.answer_requested.connect(self._retry_answer)
        self.retry_panel.undo_requested.connect(self._retry_undo)
        self.retry_panel.next_requested.connect(self._retry_next)
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
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_analysis)
        self.cancel_button.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_button)

    def _show_placeholder(self) -> None:
        self.summary.setText(
            "<h3>Load a game to start</h3>"
            "Open a <code>.pgn</code> file (Ctrl+O), paste PGN text (Ctrl+Shift+V), "
            "import from Chess.com (Ctrl+I), or drop a .pgn file on this window."
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
            f"{h.get('Date', '')} · {h.get('Result', '')} · <i>{source}</i><br>Analysing…"
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
            with open_engine() as engine:
                return analyze_game(game, engine, progress=progress, should_stop=should_stop)

        self.progress.setValue(0)
        self.progress.show()
        self.cancel_button.show()
        self.statusBar().showMessage("Analysing with Stockfish…")
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
            self.statusBar().showMessage("Analysis cancelled", 4000)
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
        self.statusBar().showMessage("Analysis complete", 4000)
        self.analysis = analysis
        self.judgements = judge_game(analysis)
        self.my_color = self._ask_my_color(analysis.headers)
        if self.my_color is not None:
            self.board.set_flipped(self.my_color == chess.BLACK)
        self.graph.set_judgements(self.judgements)
        self._populate_summary()
        self._populate_move_table()
        self._populate_mistakes()
        self.go_to(self.current_ply)

    def _ask_my_color(self, headers: dict[str, str]) -> chess.Color | None:
        """Work out which side the user played, asking if the names don't say."""
        color = player_color(headers, saved_username())
        if color is not None:
            return color
        box = QMessageBox(self)
        box.setWindowTitle("Which side were you?")
        box.setText(
            f"White: {headers.get('White', '?')}\nBlack: {headers.get('Black', '?')}\n\n"
            "Which side did you play? Your mistakes will be listed for that side."
        )
        white = box.addButton("White", QMessageBox.ButtonRole.AcceptRole)
        black = box.addButton("Black", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Both", QMessageBox.ButtonRole.RejectRole)
        last = QSettings().value(SIDE_KEY, "white")
        box.setDefaultButton(black if last == "black" else white)
        box.exec()
        if box.clickedButton() is white:
            QSettings().setValue(SIDE_KEY, "white")
            return chess.WHITE
        if box.clickedButton() is black:
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
        self.board.set_interactive(m.mover)
        self._select_mistake_item(ply)
        self.graph.set_current_ply(ply - 1)

        side = "White" if m.mover == chess.WHITE else "Black"
        color = style.judgement_color(j.judgement).name()
        self.retry_panel.set_prompt(
            f"<h3>Find a better move for {side}</h3>"
            f"In the game you played <b style='color:{color}'>{m.san}{j.judgement.symbol}</b> "
            f"({j.judgement.value}, win chance {j.win_before:.0f}% → {j.win_after:.0f}%)."
        )
        if ply in self.revealed:
            self.retry_panel.set_feedback(self._answer_text(j) + "<br>Play it on the board to check it.")
        else:
            self.retry_panel.set_feedback("Make your move on the board.")
        self.retry_panel.set_undo_enabled(False)
        self.details.hide()
        self.retry_button.hide()
        self.retry_action.setEnabled(False)
        self.retry_panel.show()

    def exit_retry(self, navigate: bool = True) -> None:
        if self.retry_ply is None:
            return
        ply = self.retry_ply
        self.retry_ply = None
        self._attempt_token += 1
        self.board.set_interactive(None)
        self.retry_panel.hide()
        self.details.show()
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
        if result.solved:
            self.solved.add(self.retry_ply)
            self._populate_mistakes()
            text = f"<span style='color:#2e8b37'><b>✓ {result.feedback()}</b></span>"
            if result.judgement is not Judgement.BEST and self.retry_ply not in self.revealed:
                text += "<br>The engine liked another move a little more. <b>Undo move</b> to look for it."
            text += "<br>Press <b>Next mistake</b> to keep going."
            self.retry_panel.set_feedback(text)
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
        return text

    def _retry_undo(self) -> None:
        if self.retry_ply is not None:
            self.start_retry(self.retry_ply)

    def _retry_answer(self) -> None:
        j = self._judgement_at(self.retry_ply)
        if j is None or not j.move.best_move_uci:
            return
        self.revealed.add(self.retry_ply)
        self._populate_mistakes()
        self.start_retry(self.retry_ply)

    def _retry_next(self) -> None:
        minimum = FILTERS[self.filter_box.currentIndex()][1]
        plies = [j.move.ply for j in flagged_moves(self.judgements, self.my_color, minimum)]
        if not plies:
            return
        current = self.retry_ply or 0
        later = [p for p in plies if p > current and p not in self.solved]
        remaining = later or [p for p in plies if p not in self.solved]
        if not remaining:
            self.retry_panel.set_feedback("<b>You've fixed every mistake in this list. 🎉</b>")
            return
        self.start_retry(remaining[0])

    def flip_board(self) -> None:
        self.board.set_flipped(not self.board.flipped)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Backspace and self.retry_ply is not None:
            if self.retry_panel.undo_button.isEnabled():
                self._retry_undo()
        elif key == Qt.Key.Key_Left:
            self.go_to(self.current_ply - 1)
        elif key == Qt.Key.Key_Right:
            self.go_to(self.current_ply + 1)
        elif key == Qt.Key.Key_Home:
            self.go_to(0)
        elif key == Qt.Key.Key_End:
            self.go_to(len(self.moves))
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.cancel_analysis()
        if self._attempt_thread and self._attempt_thread.isRunning():
            self._attempt_thread.wait()
        super().closeEvent(event)
