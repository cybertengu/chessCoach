"""A chess board widget drawn with QPainter and python-chess's SVG pieces."""

from __future__ import annotations

from dataclasses import dataclass

import chess
import chess.svg
from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import style


@dataclass(frozen=True)
class Arrow:
    start: chess.Square
    end: chess.Square
    color: QColor


class BoardWidget(QWidget):
    """Displays a position. Subclasses add interaction."""

    _renderers: dict[str, QSvgRenderer] = {}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.board = chess.Board()
        self.flipped = False
        self.highlight: tuple[chess.Square, chess.Square] | None = None
        self.highlight_color: QColor = style.LAST_MOVE
        self.arrows: list[Arrow] = []
        self.setMinimumSize(320, 320)
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    # --- public API -----------------------------------------------------

    def set_position(
        self,
        board: chess.Board,
        *,
        last_move: chess.Move | None = None,
        highlight_color: QColor | None = None,
        arrows: list[Arrow] | None = None,
    ) -> None:
        self.board = board.copy()
        self.highlight = (last_move.from_square, last_move.to_square) if last_move else None
        self.highlight_color = highlight_color or style.LAST_MOVE
        self.arrows = list(arrows or [])
        self.update()

    def set_flipped(self, flipped: bool) -> None:
        self.flipped = flipped
        self.update()

    # --- geometry -------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(560, 560)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return width

    def _geometry(self) -> tuple[float, float, float]:
        size = min(self.width(), self.height())
        sq = size / 8
        return (self.width() - size) / 2, (self.height() - size) / 2, sq

    def square_rect(self, square: chess.Square) -> QRectF:
        x0, y0, sq = self._geometry()
        file, rank = chess.square_file(square), chess.square_rank(square)
        col = 7 - file if self.flipped else file
        row = rank if self.flipped else 7 - rank
        return QRectF(x0 + col * sq, y0 + row * sq, sq, sq)

    def square_at(self, pos: QPointF) -> chess.Square | None:
        x0, y0, sq = self._geometry()
        col = int((pos.x() - x0) // sq)
        row = int((pos.y() - y0) // sq)
        if not (0 <= col < 8 and 0 <= row < 8):
            return None
        file = 7 - col if self.flipped else col
        rank = row if self.flipped else 7 - row
        return chess.square(file, rank)

    # --- painting -------------------------------------------------------

    @classmethod
    def _renderer(cls, piece: chess.Piece) -> QSvgRenderer:
        key = piece.symbol()
        if key not in cls._renderers:
            svg = chess.svg.piece(piece).encode()
            cls._renderers[key] = QSvgRenderer(QByteArray(svg))
        return cls._renderers[key]

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_squares(p)
        self.paint_overlays_below(p)
        self.paint_pieces(p)
        self.paint_arrows(p)
        self.paint_overlays_above(p)
        p.end()

    def paint_squares(self, p: QPainter) -> None:
        _, _, sq = self._geometry()
        font = QFont(self.font())
        font.setPixelSize(max(8, int(sq * 0.16)))
        font.setBold(True)
        p.setFont(font)
        for square in chess.SQUARES:
            rect = self.square_rect(square)
            light = (chess.square_file(square) + chess.square_rank(square)) % 2 == 1
            p.fillRect(rect, style.LIGHT_SQUARE if light else style.DARK_SQUARE)
            if self.highlight and square in self.highlight:
                p.fillRect(rect, self.highlight_color)

            # Coordinates on the left and bottom edges.
            text_color = style.DARK_SQUARE if light else style.LIGHT_SQUARE
            p.setPen(text_color)
            bottom_rank = 7 if self.flipped else 0
            left_file = 7 if self.flipped else 0
            pad = sq * 0.05
            if chess.square_rank(square) == bottom_rank:
                p.drawText(rect.adjusted(pad, pad, -pad, -pad),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                           chess.FILE_NAMES[chess.square_file(square)])
            if chess.square_file(square) == left_file:
                p.drawText(rect.adjusted(pad, pad, -pad, -pad),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                           chess.RANK_NAMES[chess.square_rank(square)])

        if self.board.is_check():
            king = self.board.king(self.board.turn)
            if king is not None:
                rect = self.square_rect(king)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(style.CHECK)
                p.drawEllipse(rect.adjusted(sq * 0.05, sq * 0.05, -sq * 0.05, -sq * 0.05))

    def paint_overlays_below(self, p: QPainter) -> None:
        """Hook for subclasses: drawn above squares, below pieces."""

    def paint_overlays_above(self, p: QPainter) -> None:
        """Hook for subclasses: drawn on top of everything."""

    def piece_hidden(self, square: chess.Square) -> bool:
        """Hook for subclasses (e.g. a piece being dragged)."""
        return False

    def paint_pieces(self, p: QPainter) -> None:
        for square, piece in self.board.piece_map().items():
            if not self.piece_hidden(square):
                self._renderer(piece).render(p, self.square_rect(square))

    def paint_piece_at(self, p: QPainter, piece: chess.Piece, rect: QRectF) -> None:
        self._renderer(piece).render(p, rect)

    def paint_arrows(self, p: QPainter) -> None:
        _, _, sq = self._geometry()
        for arrow in self.arrows:
            start = self.square_rect(arrow.start).center()
            end = self.square_rect(arrow.end).center()
            dx, dy = end.x() - start.x(), end.y() - start.y()
            length = (dx * dx + dy * dy) ** 0.5
            if length == 0:
                continue
            ux, uy = dx / length, dy / length
            head_len, head_w = sq * 0.38, sq * 0.32
            shaft_end = QPointF(end.x() - ux * head_len, end.y() - uy * head_len)

            pen = QPen(arrow.color, sq * 0.15)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(pen)
            p.drawLine(start, shaft_end)

            px, py = -uy, ux
            head = QPolygonF([
                end,
                QPointF(shaft_end.x() + px * head_w, shaft_end.y() + py * head_w),
                QPointF(shaft_end.x() - px * head_w, shaft_end.y() - py * head_w),
            ])
            path = QPainterPath()
            path.addPolygon(head)
            p.fillPath(path, arrow.color)
