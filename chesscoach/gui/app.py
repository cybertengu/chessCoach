"""Entry point: ``chesscoach [game.pgn]``."""

from __future__ import annotations

import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from ..engine import EngineNotFoundError, find_stockfish
from .main_window import MainWindow


# Harmless Qt desktop-integration chatter on GNOME/Wayland:
# - qt.qpa.services: the xdg portal refusing a duplicate app-ID registration.
# - qt.qpa.wayland.textinput: a known Qt bug when keyboard focus moves between
#   windows (e.g. closing a dialog).
_QUIET_RULES = "qt.qpa.services.warning=false;qt.qpa.wayland.textinput.warning=false"


def _quiet_desktop_warnings() -> None:
    """Hide known-noisy Qt log categories unless the user set their own rules."""
    if "QT_LOGGING_RULES" not in os.environ:
        os.environ["QT_LOGGING_RULES"] = _QUIET_RULES


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    _quiet_desktop_warnings()
    # Set the app identity *before* QApplication exists: Qt registers the app ID
    # with the desktop portal during start-up, and changing it afterwards makes
    # it try to register a second time ("Connection already associated...").
    QApplication.setApplicationName("chessCoach")
    QApplication.setOrganizationName("chessCoach")
    QApplication.setDesktopFileName("chesscoach")
    app = QApplication(argv)
    app.setWindowIcon(QIcon.fromTheme("chesscoach"))

    try:
        find_stockfish()
    except EngineNotFoundError as exc:
        QMessageBox.critical(None, "Stockfish not found", str(exc))
        return 1

    window = MainWindow()
    window.show()
    files = [a for a in argv[1:] if not a.startswith("-")]
    if files:
        window.load_file(files[0])
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
