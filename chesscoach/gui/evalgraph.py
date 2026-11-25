"""Win-percentage graph across the game; click to jump to a move."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..classify import MoveJudgement, win_percent
from . import style


class EvalGraph(QWidget):
    ply_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.white_win: list[float] = []  # index = ply (0 = start position)
        self.judgements: list[MoveJudgement] = []
        self.current_ply = 0
        self.setMinimumHeight(90)
        self.setMaximumHeight(140)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_judgements(self, judgements: list[MoveJudgement]) -> None:
        self.judgements = judgements
        if judgements:
            first = judgements[0].move.eval_before.cp
            self.white_win = [win_percent(first)] + [win_percent(j.move.eval_after.cp) for j in judgements]
        else:
            self.white_win = []
        self.update()

    def set_current_ply(self, ply: int) -> None:
        self.current_ply = ply
        self.update()

    def _x(self, ply: int) -> float:
        n = max(1, len(self.white_win) - 1)
        return 4 + (self.width() - 8) * ply / n

    def _y(self, win: float) -> float:
        return 4 + (self.height() - 8) * (1 - win / 100)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#3a3a3a"))
        if len(self.white_win) < 2:
            p.end()
            return

        # White's share of the graph is filled from the bottom.
        path = QPainterPath(QPointF(self._x(0), self.height()))
        for ply, win in enumerate(self.white_win):
            path.lineTo(self._x(ply), self._y(win))
        path.lineTo(self._x(len(self.white_win) - 1), self.height())
        path.closeSubpath()
        p.fillPath(path, QColor("#e8e8e8"))

        p.setPen(QPen(QColor(128, 128, 128, 160), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(0, self._y(50)), QPointF(self.width(), self._y(50)))

        for j in self.judgements:
            color = style.judgement_color(j.judgement)
            if color is None:
                continue
            ply = j.move.ply
            p.setPen(QPen(QColor("#202020"), 1))
            p.setBrush(color)
            p.drawEllipse(QPointF(self._x(ply), self._y(self.white_win[ply])), 4, 4)

        p.setPen(QPen(QColor("#3d8bfd"), 2))
        x = self._x(self.current_ply)
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if len(self.white_win) < 2:
            return
        n = len(self.white_win) - 1
        frac = (event.position().x() - 4) / max(1, self.width() - 8)
        self.ply_clicked.emit(max(0, min(n, round(frac * n))))
