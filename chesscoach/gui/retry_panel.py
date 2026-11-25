"""Panel shown while the player retries a position where they made a mistake."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class RetryPanel(QWidget):
    hint_requested = Signal()
    answer_requested = Signal()
    undo_requested = Signal()
    next_requested = Signal()
    exit_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.prompt = QLabel()
        self.prompt.setWordWrap(True)
        self.prompt.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.prompt)

        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        self.feedback.setTextFormat(Qt.TextFormat.RichText)
        self.feedback.setMinimumHeight(60)
        self.feedback.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.feedback)

        row = QHBoxLayout()
        self.undo_button = QPushButton("Undo move")
        self.undo_button.setToolTip("Take back your attempt and try another move (Backspace)")
        self.hint_button = QPushButton("Hint")
        self.hint_button.setToolTip("Highlight the piece that should move")
        self.answer_button = QPushButton("Show correct move")
        self.answer_button.setToolTip("Give up and reveal the engine's move")
        self.next_button = QPushButton("Next mistake")
        self.exit_button = QPushButton("Done")
        for button, signal in [
            (self.undo_button, self.undo_requested),
            (self.hint_button, self.hint_requested),
            (self.answer_button, self.answer_requested),
            (self.next_button, self.next_requested),
            (self.exit_button, self.exit_requested),
        ]:
            button.clicked.connect(signal.emit)
            row.addWidget(button)
        layout.addLayout(row)

    def set_undo_enabled(self, enabled: bool) -> None:
        self.undo_button.setEnabled(enabled)

    def set_prompt(self, html: str) -> None:
        self.prompt.setText(html)

    def set_feedback(self, html: str) -> None:
        self.feedback.setText(html)
