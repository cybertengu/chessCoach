"""Turn Qt key events into keymap tokens (see keymap.py)."""

from __future__ import annotations

from PySide6.QtCore import Qt

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
