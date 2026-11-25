"""Colours shared by the GUI widgets."""

from PySide6.QtGui import QColor

from ..classify import Judgement

LIGHT_SQUARE = QColor("#f0d9b5")
DARK_SQUARE = QColor("#b58863")
LAST_MOVE = QColor(205, 210, 106, 150)
CHECK = QColor(220, 40, 40, 170)

BEST_ARROW = QColor(21, 120, 27, 200)
PLAYED_ARROW = QColor(200, 50, 50, 170)

JUDGEMENT_COLORS = {
    Judgement.INACCURACY: QColor("#d4a72c"),
    Judgement.MISTAKE: QColor("#e07b24"),
    Judgement.BLUNDER: QColor("#d23c3c"),
}


def judgement_color(judgement: Judgement) -> QColor | None:
    return JUDGEMENT_COLORS.get(judgement)
