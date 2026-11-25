"""Panel shown while the player retries a position where they made a mistake."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class RetryPanel(QWidget):
    hint_requested = Signal()
    answer_requested = Signal()
    reset_requested = Signal()
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
        self.hint_button = QPushButton("Hint")
        self.answer_button = QPushButton("Show answer")
        self.reset_button = QPushButton("Try again")
        self.next_button = QPushButton("Next mistake")
        self.exit_button = QPushButton("Done")
        for button, signal in [
            (self.hint_button, self.hint_requested),
            (self.answer_button, self.answer_requested),
            (self.reset_button, self.reset_requested),
            (self.next_button, self.next_requested),
            (self.exit_button, self.exit_requested),
        ]:
            button.clicked.connect(signal.emit)
            row.addWidget(button)
        layout.addLayout(row)

    def set_prompt(self, html: str) -> None:
        self.prompt.setText(html)

    def set_feedback(self, html: str) -> None:
        self.feedback.setText(html)
