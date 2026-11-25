"""Reading games from PGN text."""

from __future__ import annotations

import io
from pathlib import Path

import chess.pgn


class PGNError(ValueError):
    """Raised when PGN text cannot be turned into a playable game."""


def read_games(text: str) -> list[chess.pgn.Game]:
    """Parse every game in ``text``. Raises :class:`PGNError` if none are usable."""
    stream = io.StringIO(text)
    games: list[chess.pgn.Game] = []
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        if game.errors:
            raise PGNError(f"Could not read the PGN: {game.errors[0]}")
        if game.end().ply() > game.ply():
            games.append(game)
    if not games:
        raise PGNError("No games with moves were found in the PGN text.")
    return games


def read_game(text: str) -> chess.pgn.Game:
    """Parse the first game in ``text``."""
    return read_games(text)[0]


def read_game_file(path: str | Path) -> chess.pgn.Game:
    return read_game(Path(path).read_text(encoding="utf-8", errors="replace"))
