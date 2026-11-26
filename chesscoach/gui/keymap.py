"""Vim-style key bindings, kept free of Qt so they can be unit-tested.

Keys arrive as *tokens*: the typed character for printable keys (``"h"``,
``"G"``, ``"?"``) or a name for special keys (``"Esc"``, ``"Enter"``,
``"Space"``, ``"Left"``...). :class:`KeyParser` turns a stream of tokens into
:class:`Command` objects, handling counts (``5l``) and the ``gg`` prefix.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import chess


class Mode(Enum):
    NORMAL = "normal"  # browsing a game
    PUZZLE = "puzzle"  # retrying one of your mistakes


@dataclass(frozen=True)
class Binding:
    keys: tuple[str, ...]  # every key that triggers the command
    command: str
    help: str


# Commands available in both modes.
_COMMON = [
    Binding(("f",), "flip", "Flip the board"),
    Binding(("?",), "help", "Show this list of keys"),
    Binding((":",), "cmdline", "Command line: a move like :Nf3, or :e :paste :cc :report :q :help"),
    Binding(("n",), "next_mistake", "Next of your mistakes (starts the puzzle)"),
    Binding(("N",), "prev_mistake", "Previous of your mistakes"),
    Binding(("c",), "cycle_filter", "Cycle the mistake filter (all / mistakes+ / blunders)"),
]

BINDINGS: dict[Mode, list[Binding]] = {
    Mode.NORMAL: [
        Binding(("h", "Left"), "prev", "Previous half-move"),
        Binding(("l", "Right"), "next", "Next half-move"),
        Binding(("k", "Up"), "up", "Up one full move"),
        Binding(("j", "Down"), "down", "Down one full move"),
        Binding(("gg", "Home"), "start", "Start of the game (count: go to that move)"),
        Binding(("G", "End"), "end", "End of the game (count: go to that move)"),
        Binding(("t", "Enter"), "retry", "Try a better move for the error shown"),
        Binding(("Esc",), "cancel", "Cancel a running analysis"),
        *_COMMON,
    ],
    Mode.PUZZLE: [
        Binding(("h", "Left"), "cursor_left", "Move the square cursor left"),
        Binding(("l", "Right"), "cursor_right", "Move the square cursor right"),
        Binding(("k", "Up"), "cursor_up", "Move the square cursor up"),
        Binding(("j", "Down"), "cursor_down", "Move the square cursor down"),
        Binding(("Space",), "pick", "Pick up / drop the piece under the cursor"),
        Binding(("u", "Backspace"), "undo", "Undo your attempt"),
        Binding(("H",), "hint", "Hint: highlight the piece to move"),
        Binding(("s",), "solution", "Show the correct move"),
        Binding(("q", "Esc"), "done", "Done: leave the puzzle"),
        *_COMMON,
    ],
}

# Shortcuts handled by Qt actions (with modifiers), listed for the help screen.
CTRL_SHORTCUTS = [
    ("Ctrl+O", "Open a PGN file  (:e)"),
    ("Ctrl+Shift+V", "Paste PGN text  (:paste)"),
    ("Ctrl+I", "Import from Chess.com  (:cc)"),
    ("Ctrl+R", "My mistakes: the report  (:report)"),
    ("Ctrl+Q", "Quit  (:q)"),
]


@dataclass(frozen=True)
class Command:
    name: str
    count: int | None = None  # None when no count was typed

    @property
    def n(self) -> int:
        return self.count or 1


class KeyParser:
    """Accumulates tokens into commands, vim-style."""

    def __init__(self) -> None:
        self.pending = ""  # typed count digits and/or a "g" prefix
        self._tables = {
            mode: {key: b.command for b in bindings for key in b.keys}
            for mode, bindings in BINDINGS.items()
        }

    def reset(self) -> None:
        self.pending = ""

    def handles(self, token: str, mode: Mode) -> bool:
        """Whether ``token`` means something in ``mode`` (so it should be swallowed)."""
        table = self._tables[mode]
        if token in table or (token == "Esc" and self.pending):
            return True
        if token.isdigit() and len(token) == 1:
            return True
        return token == "g" and "gg" in table or ("g" + token) in table and self.pending.endswith("g")

    def feed(self, token: str, mode: Mode) -> Command | None:
        table = self._tables[mode]
        digits = "".join(ch for ch in self.pending if ch.isdigit())

        if token == "Esc" and self.pending:
            self.reset()  # Esc first clears a half-typed command, like vim
            return None
        if token.isdigit() and len(token) == 1 and (digits or token != "0"):
            self.pending += token
            return None

        sequence = ("g" if self.pending.endswith("g") else "") + token
        if sequence == "g" and "gg" in table:
            self.pending += "g"
            return None

        self.reset()
        name = table.get(sequence)
        if name is None:
            return None
        return Command(name, int(digits) if digits else None)


def check_no_clashes() -> list[str]:
    """Return descriptions of any key bound twice within one mode."""
    problems = []
    for mode, bindings in BINDINGS.items():
        seen: dict[str, str] = {}
        for b in bindings:
            for key in b.keys:
                if key in seen:
                    problems.append(f"{mode.value}: {key!r} -> {seen[key]} and {b.command}")
                seen[key] = b.command
        # A single "g" binding would make the "gg" prefix ambiguous.
        if "g" in seen and any(k.startswith("g") and len(k) > 1 for k in seen):
            problems.append(f"{mode.value}: 'g' clashes with the gg prefix")
    return problems


# --- command line -------------------------------------------------------

LINE_COMMANDS = {
    "e": "open", "edit": "open", "open": "open",
    "paste": "paste",
    "cc": "chesscom", "chesscom": "chesscom", "import": "chesscom",
    "q": "quit", "quit": "quit", "qa": "quit",
    "help": "help", "h": "help",
    "flip": "flip",
    "report": "report", "mistakes": "report",
}


@dataclass(frozen=True)
class LineResult:
    command: str | None = None  # e.g. "open", "quit"
    move: chess.Move | None = None
    error: str | None = None


def parse_command_line(text: str, board: chess.Board | None) -> LineResult:
    """Interpret what was typed after ``:``: an app command or a chess move."""
    text = text.strip().lstrip(":").strip()
    if not text:
        return LineResult()
    if text.lower() in LINE_COMMANDS:
        return LineResult(command=LINE_COMMANDS[text.lower()])
    if board is None:
        return LineResult(error=f"Not a command: {text}")
    try:
        return LineResult(move=board.parse_san(text))
    except ValueError:
        pass
    try:
        move = chess.Move.from_uci(text.lower())
        if move in board.legal_moves:
            return LineResult(move=move)
    except ValueError:
        pass
    return LineResult(error=f"Not a legal move or command: {text}")
