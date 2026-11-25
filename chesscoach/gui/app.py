"""Entry point: ``chesscoach [game.pgn]``."""

from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from ..engine import EngineNotFoundError, find_stockfish
from .main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("chessCoach")
    app.setOrganizationName("chessCoach")
    app.setDesktopFileName("chesscoach")
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
