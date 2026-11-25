"""A board the user can make moves on (click-click or drag-and-drop)."""

from __future__ import annotations

import chess
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

from .board import BoardWidget

SELECTED = QColor(20, 85, 30, 110)
TARGET_DOT = QColor(20, 85, 30, 120)
HINT = QColor(60, 140, 255, 140)
CURSOR = QColor(40, 110, 230)


class InteractiveBoard(BoardWidget):
    move_made = Signal(object)  # chess.Move

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.interactive_color: chess.Color | None = None  # None = not interactive
        self.selected: chess.Square | None = None
        self.hint_square: chess.Square | None = None
        self._drag_from: chess.Square | None = None
        self._drag_pos: QPointF | None = None
        # Keyboard cursor (h/j/k/l + Space). Shown once a cursor key is used.
        self.cursor: chess.Square | None = None
        self.cursor_visible = False
        self.setMouseTracking(True)

    def set_interactive(self, color: chess.Color | None) -> None:
        self.interactive_color = color
        self.selected = None
        self.hint_square = None
        self._drag_from = None
        if color is not None:
            king = self.board.king(color)
            self.cursor = king if king is not None else chess.E4
        self.setCursor(Qt.CursorShape.PointingHandCursor if color is not None else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_hint(self, square: chess.Square | None) -> None:
        self.hint_square = square
        self.update()

    # --- keyboard -------------------------------------------------------

    def move_cursor(self, right: int, up: int) -> None:
        """Move the cursor as seen on screen (so 'up' is always towards the top)."""
        if self.interactive_color is None or self.cursor is None:
            return
        if self.flipped:
            right, up = -right, -up
        file = max(0, min(7, chess.square_file(self.cursor) + right))
        rank = max(0, min(7, chess.square_rank(self.cursor) + up))
        self.cursor = chess.square(file, rank)
        self.cursor_visible = True
        self.update()

    def press_cursor(self) -> None:
        """Space: pick up the piece under the cursor, or drop the held one there."""
        if self.interactive_color is None or self.cursor is None:
            return
        self.cursor_visible = True
        square = self.cursor
        if self.selected is not None and square != self.selected and self._try_move(self.selected, square):
            self.update()
            return
        if self.selected == square:
            self.selected = None  # put it back down
        elif self._can_move_from(square):
            self.selected = square
        self.update()

    # --- helpers --------------------------------------------------------

    def _can_move_from(self, square: chess.Square | None) -> bool:
        if square is None or self.interactive_color is None or self.board.turn != self.interactive_color:
            return False
        piece = self.board.piece_at(square)
        return piece is not None and piece.color == self.interactive_color

    def _targets(self, square: chess.Square) -> set[chess.Square]:
        return {m.to_square for m in self.board.legal_moves if m.from_square == square}

    def _try_move(self, start: chess.Square, end: chess.Square) -> bool:
        candidates = [m for m in self.board.legal_moves if m.from_square == start and m.to_square == end]
        if not candidates:
            return False
        move = candidates[0]
        if len(candidates) > 1:  # promotion: let the player choose
            move = self._ask_promotion(candidates)
            if move is None:
                return True  # handled (cancelled)
        self.selected = None
        self.move_made.emit(move)
        return True

    def _ask_promotion(self, candidates: list[chess.Move]) -> chess.Move | None:
        menu = QMenu(self)
        names = {chess.QUEEN: "&Queen", chess.ROOK: "&Rook", chess.BISHOP: "&Bishop", chess.KNIGHT: "K&night"}
        actions = {}
        for move in sorted(candidates, key=lambda m: -(m.promotion or 0)):
            actions[menu.addAction(names.get(move.promotion, "?"))] = move
        chosen = menu.exec(self.mapToGlobal(self.square_rect(candidates[0].to_square).center().toPoint()))
        return actions.get(chosen)

    # --- painting -------------------------------------------------------

    def paint_overlays_below(self, p: QPainter) -> None:
        if self.hint_square is not None:
            p.fillRect(self.square_rect(self.hint_square), HINT)
        source = self._drag_from if self._drag_from is not None else self.selected
        if source is None:
            return
        p.fillRect(self.square_rect(source), SELECTED)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(TARGET_DOT)
        for target in self._targets(source):
            rect = self.square_rect(target)
            r = rect.width() * (0.42 if self.board.piece_at(target) else 0.16)
            p.drawEllipse(rect.center(), r, r)

    def piece_hidden(self, square: chess.Square) -> bool:
        return square == self._drag_from and self._drag_pos is not None

    def paint_overlays_above(self, p: QPainter) -> None:
        if self.cursor_visible and self.interactive_color is not None and self.cursor is not None:
            rect = self.square_rect(self.cursor)
            width = max(3.0, rect.width() * 0.07)
            pen = QPen(CURSOR, width)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect.adjusted(width / 2, width / 2, -width / 2, -width / 2))
        if self._drag_from is None or self._drag_pos is None:
            return
        piece = self.board.piece_at(self._drag_from)
        if piece:
            size = self.square_rect(self._drag_from).width()
            rect = QRectF(self._drag_pos.x() - size / 2, self._drag_pos.y() - size / 2, size, size)
            self.paint_piece_at(p, piece, rect)

    # --- mouse ----------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.interactive_color is None or event.button() != Qt.MouseButton.LeftButton:
            return
        square = self.square_at(event.position())
        self.cursor_visible = False
        if square is not None:
            self.cursor = square
        if self.selected is not None and square is not None and self._try_move(self.selected, square):
            self.update()
            return
        if self._can_move_from(square):
            self.selected = square
            self._drag_from = square
        else:
            self.selected = None
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._drag_pos = event.position()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_from is None:
            return
        start, dragged = self._drag_from, self._drag_pos is not None
        self._drag_from = None
        self._drag_pos = None
        if dragged:
            square = self.square_at(event.position())
            if square is not None and square != start:
                self._try_move(start, square)
        self.update()
