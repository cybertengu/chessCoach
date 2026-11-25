"""Dialogs for getting a game into the app."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractButton,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..chesscom import ChessComGame, recent_games
from .keymap import BINDINGS, CTRL_SHORTCUTS, LINE_COMMANDS, Mode
from .tasks import TaskThread

USERNAME_KEY = "chesscom/username"


def saved_username() -> str:
    return str(QSettings().value(USERNAME_KEY, "") or "")


class VimListWidget(QListWidget):
    """A list that also understands j/k/gg/G, and '/' to jump to a search box."""

    slash_pressed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._g = False

    def keyPressEvent(self, event) -> None:  # noqa: N802
        text = event.text()
        moves = {"j": 1, "k": -1}
        if text in moves and self.count():
            self.setCurrentRow(max(0, min(self.count() - 1, self.currentRow() + moves[text])))
        elif text == "G" and self.count():
            self.setCurrentRow(self.count() - 1)
        elif text == "g":
            if self._g and self.count():
                self.setCurrentRow(0)
            self._g = not self._g
            return
        elif text == "/":
            self.slash_pressed.emit()
        else:
            super().keyPressEvent(event)
        self._g = False


class SideDialog(QMessageBox):
    """'Which side were you?' with w / b / a keys."""

    def __init__(self, white_name: str, black_name: str, default_black: bool, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Which side were you?")
        self.setText(
            f"White: {white_name}\nBlack: {black_name}\n\n"
            "Which side did you play? Your mistakes will be listed for that side."
        )
        self.white = self.addButton("White  (w)", QMessageBox.ButtonRole.AcceptRole)
        self.black = self.addButton("Black  (b)", QMessageBox.ButtonRole.AcceptRole)
        self.both = self.addButton("Both  (a)", QMessageBox.ButtonRole.RejectRole)
        self.setDefaultButton(self.black if default_black else self.white)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        button: QAbstractButton | None = {"w": self.white, "b": self.black, "a": self.both}.get(
            event.text().lower()
        )
        if button is not None:
            button.click()
            return
        super().keyPressEvent(event)


def _keys_html(keys: tuple[str, ...]) -> str:
    return " ".join(f"<code>{k}</code>" for k in keys)


def key_help_html() -> str:
    """Cheat sheet built from the live bindings, so it can never go stale."""
    titles = {Mode.NORMAL: "Browsing a game", Mode.PUZZLE: "Puzzle (retrying a mistake)"}
    parts = []
    for mode, bindings in BINDINGS.items():
        rows = "".join(
            f"<tr><td style='padding-right:14px'>{_keys_html(b.keys)}</td><td>{b.help}</td></tr>"
            for b in bindings
        )
        parts.append(f"<h3>{titles[mode]}</h3><table>{rows}</table>")
    parts.append("<p>A number before a motion repeats it: <code>5l</code>, <code>3j</code>; "
                 "<code>12G</code> jumps to move 12.</p>")
    cmds: dict[str, list[str]] = {}
    for word, action in LINE_COMMANDS.items():
        cmds.setdefault(action, []).append(":" + word)
    rows = "".join(
        f"<tr><td style='padding-right:14px'><code>{' '.join(words)}</code></td><td>{action}</td></tr>"
        for action, words in cmds.items()
    )
    rows += ("<tr><td style='padding-right:14px'><code>:Nf3</code> <code>:e2e4</code></td>"
             "<td>play a move (puzzle mode)</td></tr>")
    parts.append(f"<h3>Command line ( : )</h3><table>{rows}</table>")
    rows = "".join(
        f"<tr><td style='padding-right:14px'><code>{k}</code></td><td>{h}</td></tr>" for k, h in CTRL_SHORTCUTS
    )
    parts.append(f"<h3>Also</h3><table>{rows}</table>"
                 "<p>Dialogs: <code>j</code>/<code>k</code> move in lists, <code>Enter</code> confirms, "
                 "<code>Esc</code> cancels; side picker uses <code>w</code>/<code>b</code>/<code>a</code>; "
                 "promotion menu uses <code>q</code>/<code>r</code>/<code>b</code>/<code>n</code>.</p>")
    return "".join(parts)


class HelpDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.resize(560, 640)
        layout = QVBoxLayout(self)
        label = QLabel(key_help_html())
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(label)
        layout.addWidget(scroll)
        layout.addWidget(QLabel("Close with Esc, q or ?"))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.text() in ("q", "?") or event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept()
            return
        super().keyPressEvent(event)


class PastePGNDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Paste PGN")
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Paste the PGN text of one game:"))
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText('[Event "..."]\n\n1. e4 e5 2. Nf3 ...')
        layout.addWidget(self.editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Analyse  (Ctrl+Enter)")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel  (Esc)")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        # Enter makes a new line in the editor, so analysing needs Ctrl+Enter.
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(keys), self, activated=self.accept)

    def pgn_text(self) -> str:
        return self.editor.toPlainText()


class ChessComDialog(QDialog):
    """Fetch a user's recent games and pick one to analyse."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import from Chess.com")
        self.resize(620, 460)
        self._thread: TaskThread | None = None
        self.selected: ChessComGame | None = None

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Username:"))
        self.username = QLineEdit(saved_username())
        self.username.setPlaceholderText("your Chess.com username")
        row.addWidget(self.username, 1)
        row.addWidget(QLabel("Games:"))
        self.count = QSpinBox()
        self.count.setRange(1, 100)
        self.count.setValue(20)
        row.addWidget(self.count)
        self.fetch_button = QPushButton("Fetch")
        self.fetch_button.clicked.connect(self.fetch)
        row.addWidget(self.fetch_button)
        layout.addLayout(row)

        self.games = VimListWidget()
        self.games.itemDoubleClicked.connect(lambda _item: self.accept())
        self.games.slash_pressed.connect(self.username.setFocus)
        layout.addWidget(self.games, 1)
        self.status = QLabel("")
        layout.addWidget(self.status)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Analyse  (Enter)")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel  (Esc)")
        self.fetch_button.setText("Fetch  (Enter)")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        if self.username.text():
            self.fetch()

    def fetch(self) -> None:
        name = self.username.text().strip()
        if not name or (self._thread and self._thread.isRunning()):
            return
        QSettings().setValue(USERNAME_KEY, name)
        count = self.count.value()
        self.fetch_button.setEnabled(False)
        self.status.setText("Fetching games…")
        self._thread = TaskThread(lambda _progress, _stop: recent_games(name, count), self)
        self._thread.succeeded.connect(lambda games: self._show_games(name, games))
        self._thread.failed.connect(self._show_error)
        self._thread.finished.connect(lambda: self.fetch_button.setEnabled(True))
        self._thread.start()

    def _show_games(self, name: str, games: list[ChessComGame]) -> None:
        self.games.clear()
        for game in games:
            item = QListWidgetItem(game.describe(name))
            item.setData(Qt.ItemDataRole.UserRole, game)
            self.games.addItem(item)
        if games:
            self.games.setCurrentRow(0)
            self.games.setFocus()
        self.status.setText(
            f"{len(games)} games. j/k to choose, Enter to analyse, / to change the username."
        )

    def _show_error(self, message: str) -> None:
        self.status.setText(f"⚠ {message}")

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Enter in the username box fetches instead of accepting the dialog.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            self.username.hasFocus() or self.count.hasFocus()
        ):
            self.fetch()
            return
        super().keyPressEvent(event)

    def accept(self) -> None:
        item = self.games.currentItem()
        if item is None:
            return
        self.selected = item.data(Qt.ItemDataRole.UserRole)
        super().accept()

    def done(self, result: int) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.requestInterruption()
            self._thread.wait(25_000)
        super().done(result)
