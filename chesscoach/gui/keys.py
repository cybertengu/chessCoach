"""Keyboard helpers shared by the windows: key tokens (see keymap.py) and the ':' line."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit

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
