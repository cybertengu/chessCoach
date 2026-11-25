"""Dialogs for getting a game into the app."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..chesscom import ChessComGame, recent_games
from .tasks import TaskThread

USERNAME_KEY = "chesscom/username"


def saved_username() -> str:
    return str(QSettings().value(USERNAME_KEY, "") or "")


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
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Analyse")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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
        self.username.returnPressed.connect(self.fetch)
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

        self.games = QListWidget()
        self.games.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self.games, 1)
        self.status = QLabel("")
        layout.addWidget(self.status)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Analyse")
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
        self.status.setText(f"{len(games)} games. Double-click one to analyse it.")

    def _show_error(self, message: str) -> None:
        self.status.setText(f"⚠ {message}")

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
