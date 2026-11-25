"""A board the user can make moves on (click-click or drag-and-drop)."""

from __future__ import annotations

import chess
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

from . import style
from .board import BoardWidget

SELECTED = QColor(20, 85, 30, 110)
TARGET_DOT = QColor(20, 85, 30, 120)
HINT = QColor(60, 140, 255, 140)
CURSOR = QColor(40, 110, 230)
GHOST_OPACITY = 0.45

PIECE_NAMES = {
    chess.PAWN: "Pawn", chess.KNIGHT: "Knight", chess.BISHOP: "Bishop",
    chess.ROOK: "Rook", chess.QUEEN: "Queen", chess.KING: "King",
}


class InteractiveBoard(BoardWidget):
    """Playable board.

    While a piece is held (picked up with Space, clicked, or being dragged) the
    square under the keyboard cursor or mouse is *previewed*: a blue arrow and a
    see-through copy of the piece show where it would go before anything moves.
    """

    move_made = Signal(object)  # chess.Move
    status_changed = Signal(str)  # plain-language description of cursor/preview

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
        self._hover: chess.Square | None = None  # square under the mouse
        self.preview_override: chess.Move | None = None  # e.g. a move being typed
        self._status = ""
        self.setMouseTracking(True)

    def set_interactive(self, color: chess.Color | None) -> None:
        self.interactive_color = color
        self.selected = None
        self.hint_square = None
        self._drag_from = None
        self._drag_pos = None
        self.preview_override = None
        self.cursor_visible = False
        if color is not None:
            king = self.board.king(color)
            self.cursor = king if king is not None else chess.E4
        self.setCursor(Qt.CursorShape.PointingHandCursor if color is not None else Qt.CursorShape.ArrowCursor)
        self._changed()

    def set_hint(self, square: chess.Square | None) -> None:
        self.hint_square = square
        self.update()

    def set_preview_override(self, move: chess.Move | None) -> None:
        """Preview a specific move (used while typing ':Nf3'). Illegal moves are ignored."""
        self.preview_override = move if move is not None and move in self.board.legal_moves else None
        self._changed()

    # --- preview --------------------------------------------------------

    def _target_square(self) -> chess.Square | None:
        """Where the held piece would go: drag position, keyboard cursor, or mouse hover."""
        if self._drag_from is not None and self._drag_pos is not None:
            return self.square_at(self._drag_pos)
        if self.cursor_visible:
            return self.cursor
        return self._hover

    def preview_move(self) -> chess.Move | None:
        """The move that would be played right now, or None."""
        if self.interactive_color is None:
            return None
        if self.preview_override is not None:
            return self.preview_override
        source = self._drag_from if self._drag_from is not None else self.selected
        target = self._target_square()
        if source is None or target is None or source == target:
            return None
        candidates = [m for m in self.board.legal_moves if m.from_square == source and m.to_square == target]
        if not candidates:
            return None
        # For a promotion, preview the queen.
        return max(candidates, key=lambda m: m.promotion or 0)

    def describe(self) -> str:
        """Plain-language status, e.g. 'Knight g1 → f3  (Nf3)'."""
        if self.interactive_color is None:
            return ""
        move = self.preview_move()
        if move is not None:
            piece = self.board.piece_at(move.from_square)
            name = PIECE_NAMES[piece.piece_type] if piece else "Piece"
            verb = "takes on" if self.board.is_capture(move) else "→"
            return (f"{name} {chess.square_name(move.from_square)} {verb} "
                    f"{chess.square_name(move.to_square)}   ({self.board.san(move)})")
        source = self._drag_from if self._drag_from is not None else self.selected
        target = self._target_square()
        if source is not None:
            piece = self.board.piece_at(source)
            name = PIECE_NAMES[piece.piece_type] if piece else "Piece"
            held = f"Holding {name} on {chess.square_name(source)}"
            if target is not None and target != source:
                return f"{held}: can't go to {chess.square_name(target)}"
            return held + ": choose a square"
        if self.cursor_visible and self.cursor is not None:
            piece = self.board.piece_at(self.cursor)
            on = f" ({'your' if piece.color == self.interactive_color else 'their'} "\
                 f"{PIECE_NAMES[piece.piece_type].lower()})" if piece else ""
            return f"Cursor on {chess.square_name(self.cursor)}{on}"
        if self._hover is not None:
            return f"Square {chess.square_name(self._hover)}"
        return ""

    def _changed(self) -> None:
        self.update()
        status = self.describe()
        if status != self._status:
            self._status = status
            self.status_changed.emit(status)

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
        self._changed()

    def press_cursor(self) -> None:
        """Space: pick up the piece under the cursor, or drop the held one there."""
        if self.interactive_color is None or self.cursor is None:
            return
        self.cursor_visible = True
        square = self.cursor
        if self.selected is not None and square != self.selected and self._try_move(self.selected, square):
            self._changed()
            return
        if self.selected == square:
            self.selected = None  # put it back down
        elif self._can_move_from(square):
            self.selected = square
        self._changed()

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

    def paint_preview(self, p: QPainter) -> None:
        move = self.preview_move()
        if move is None:
            return
        self.paint_arrow(p, move.from_square, move.to_square, style.PREVIEW_ARROW)
        dragging = self._drag_from is not None and self._drag_pos is not None
        piece = self.board.piece_at(move.from_square)
        if piece is not None and not dragging:  # while dragging the real piece is already there
            if move.promotion:
                piece = chess.Piece(move.promotion, piece.color)
            p.save()
            p.setOpacity(GHOST_OPACITY)
            self.paint_piece_at(p, piece, self.square_rect(move.to_square))
            p.restore()

    def paint_overlays_above(self, p: QPainter) -> None:
        self.paint_preview(p)
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
            self._changed()
            return
        if self._can_move_from(square):
            self.selected = square
            self._drag_from = square
        else:
            self.selected = None
        self._changed()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        square = self.square_at(event.position())
        moved_square = square != self._hover
        self._hover = square
        if self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._drag_pos = event.position()
            self.cursor_visible = False
            self._changed()
        elif moved_square and self.interactive_color is not None:
            self.cursor_visible = False  # the mouse takes over from the keyboard cursor
            self._changed()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = None
        self._changed()
        super().leaveEvent(event)

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
        self._changed()
