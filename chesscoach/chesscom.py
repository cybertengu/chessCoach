"""Download a player's games from the public Chess.com API.

Docs: https://www.chess.com/news/view/published-data-api
No login is needed; games are grouped into monthly archives.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

API_ROOT = "https://api.chess.com/pub/player"
USER_AGENT = "chessCoach/0.1"

Fetcher = Callable[[str], dict]


class ChessComError(RuntimeError):
    pass


def _http_get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ChessComError("Chess.com user not found.") from exc
        if exc.code == 429:
            raise ChessComError("Chess.com is rate limiting requests; try again in a minute.") from exc
        raise ChessComError(f"Chess.com returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise ChessComError(f"Could not reach Chess.com: {exc.reason}") from exc


@dataclass(frozen=True)
class ChessComGame:
    url: str
    pgn: str
    white: str
    black: str
    white_result: str
    black_result: str
    white_rating: int | None
    black_rating: int | None
    time_class: str
    time_control: str
    end_time: datetime | None
    rules: str

    def opponent_of(self, username: str) -> str:
        return self.black if self.white.lower() == username.lower() else self.white

    def result_for(self, username: str) -> str:
        """'win', 'loss' or 'draw' from ``username``'s point of view."""
        mine = self.white_result if self.white.lower() == username.lower() else self.black_result
        if mine == "win":
            return "win"
        if mine in {"agreed", "repetition", "stalemate", "insufficient",
                    "50move", "timevsinsufficient"}:
            return "draw"
        return "loss"

    def describe(self, username: str) -> str:
        """One-line summary like '2026-11-24 rapid vs someone (win)'."""
        when = self.end_time.strftime("%Y-%m-%d %H:%M") if self.end_time else "?"
        color = "White" if self.white.lower() == username.lower() else "Black"
        return (
            f"{when}  {self.time_class:<6} as {color} vs {self.opponent_of(username)}"
            f"  ({self.result_for(username)})"
        )

    @classmethod
    def from_api(cls, data: dict) -> "ChessComGame":
        white, black = data.get("white", {}), data.get("black", {})
        end = data.get("end_time")
        return cls(
            url=data.get("url", ""),
            pgn=data.get("pgn", ""),
            white=white.get("username", "?"),
            black=black.get("username", "?"),
            white_result=white.get("result", ""),
            black_result=black.get("result", ""),
            white_rating=white.get("rating"),
            black_rating=black.get("rating"),
            time_class=data.get("time_class", ""),
            time_control=data.get("time_control", ""),
            end_time=datetime.fromtimestamp(end, tz=timezone.utc) if end else None,
            rules=data.get("rules", "chess"),
        )


def recent_games(
    username: str,
    count: int = 20,
    *,
    fetch: Fetcher = _http_get_json,
) -> list[ChessComGame]:
    """Most recent standard-chess games for ``username``, newest first."""
    username = username.strip()
    if not username:
        raise ChessComError("Enter a Chess.com username.")
    archives = fetch(f"{API_ROOT}/{username.lower()}/games/archives").get("archives", [])

    games: list[ChessComGame] = []
    for archive_url in reversed(archives):
        month = [ChessComGame.from_api(g) for g in fetch(archive_url).get("games", [])]
        # Variants (chess960, bughouse...) can't be analyzed as normal games.
        month = [g for g in month if g.rules == "chess" and g.pgn]
        month.sort(key=lambda g: g.end_time or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        games.extend(month)
        if len(games) >= count:
            break
    return games[:count]
