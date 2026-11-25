"""Command-line interface: ``chesscoach-cli analyze game.pgn``."""

from __future__ import annotations

import argparse
import json
import sys

import chess

from .analysis import DEFAULT_NODES, GameAnalysis
from .cache import AnalysisCache, analyze_with_cache
from .chesscom import ChessComError, recent_games
from .classify import judge_game, player_color, side_accuracy
from .engine import EngineNotFoundError
from .pgn import PGNError, read_game_file


def _plural(n: int, word: str) -> str:
    if n == 1:
        return f"1 {word}"
    return f"{n} {word[:-1]}ies" if word.endswith("y") else f"{n} {word}s"


def _print_table(result: GameAnalysis, username: str | None) -> None:
    judgements = judge_game(result)
    mine = player_color(result.headers, username)
    for j in judgements:
        m = j.move
        label = f"{m.label}{j.judgement.symbol}"
        note = ""
        if j.judgement.is_error:
            note = f"{j.judgement.value:<10} (-{j.win_loss:.0f}%)  best: {m.best_move_san}"
            if mine is not None and m.mover == mine:
                note = "<<< " + note
        print(f"{label:<16} {str(m.eval_after):>8}  {note}")

    print()
    for color, name in ((chess.WHITE, "White"), (chess.BLACK, "Black")):
        acc = side_accuracy(judgements, color)
        counts = {k: 0 for k in ("inaccuracy", "mistake", "blunder")}
        for j in judgements:
            if j.move.mover == color and j.judgement.value in counts:
                counts[j.judgement.value] += 1
        player = result.headers.get(name, "?")
        acc_text = f"{acc:.1f}%" if acc is not None else "-"
        tally = ", ".join(
            _plural(counts[k], k) for k in ("inaccuracy", "mistake", "blunder")
        )
        print(f"{name} ({player}): accuracy {acc_text}, {tally}")


def _cmd_analyze(args: argparse.Namespace) -> int:
    game = read_game_file(args.pgn)

    def progress(done: int, total: int) -> None:
        print(f"\rAnalysing position {done}/{total}", end="", file=sys.stderr, flush=True)

    result = analyze_with_cache(
        game,
        cache=None if args.no_cache else AnalysisCache(),
        nodes=args.nodes,
        depth=args.depth,
        engine_count=args.engines,
        engine_path=args.engine,
        progress=progress,
    )
    print(file=sys.stderr)

    if args.json:
        json.dump(result.to_dict(), sys.stdout, indent=2)
        print()
    else:
        _print_table(result, args.user)
    return 0


def _cmd_recent(args: argparse.Namespace) -> int:
    games = recent_games(args.username, count=args.count)
    if not games:
        print("No games found.")
        return 0
    for i, game in enumerate(games, 1):
        print(f"{i:>3}. {game.describe(args.username)}  {game.url}")
    if args.save:
        from pathlib import Path

        out = Path(args.save)
        out.mkdir(parents=True, exist_ok=True)
        for game in games:
            stamp = game.end_time.strftime("%Y%m%d-%H%M") if game.end_time else "unknown"
            name = f"{stamp}-{game.white}-vs-{game.black}.pgn"
            (out / name).write_text(game.pgn, encoding="utf-8")
        print(f"Saved {len(games)} PGN files to {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chesscoach-cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="Analyse a PGN file with Stockfish")
    analyze.add_argument("pgn", help="Path to a .pgn file")
    analyze.add_argument(
        "--nodes",
        type=int,
        default=DEFAULT_NODES,
        help=f"Nodes searched per position (default {DEFAULT_NODES:,})",
    )
    analyze.add_argument("--depth", type=int, help="Optional depth cap per position")
    analyze.add_argument("--engine", help="Path to the Stockfish binary")
    analyze.add_argument(
        "--engines",
        type=int,
        help="Number of Stockfish processes to run in parallel (default: one per CPU core)",
    )
    analyze.add_argument(
        "--no-cache", action="store_true", help="Re-analyse even if this game was analysed before"
    )
    analyze.add_argument("--user", help="Your username, to mark your own mistakes")
    analyze.add_argument("--json", action="store_true", help="Print the full analysis as JSON")
    analyze.set_defaults(func=_cmd_analyze)

    recent = sub.add_parser("recent", help="List your recent Chess.com games")
    recent.add_argument("username", help="Chess.com username")
    recent.add_argument("--count", type=int, default=10, help="How many games to list")
    recent.add_argument("--save", metavar="DIR", help="Also save each game as a .pgn file in DIR")
    recent.set_defaults(func=_cmd_recent)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (PGNError, EngineNotFoundError, ChessComError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
