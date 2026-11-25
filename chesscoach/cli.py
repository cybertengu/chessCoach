"""Command-line interface: ``chesscoach-cli analyze game.pgn``."""

from __future__ import annotations

import argparse
import json
import sys

import chess

from .analysis import DEFAULT_DEPTH, DEFAULT_MAX_SECONDS, GameAnalysis, analyze_game
from .classify import judge_game, player_color, side_accuracy
from .engine import EngineNotFoundError, open_engine
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

    with open_engine(args.engine) as engine:
        result = analyze_game(
            game, engine, depth=args.depth, max_seconds=args.max_seconds, progress=progress
        )
    print(file=sys.stderr)

    if args.json:
        json.dump(result.to_dict(), sys.stdout, indent=2)
        print()
    else:
        _print_table(result, args.user)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chesscoach-cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="Analyse a PGN file with Stockfish")
    analyze.add_argument("pgn", help="Path to a .pgn file")
    analyze.add_argument("--depth", type=int, default=DEFAULT_DEPTH, help="Search depth per position")
    analyze.add_argument(
        "--max-seconds",
        type=float,
        default=DEFAULT_MAX_SECONDS,
        help="Maximum thinking time per position",
    )
    analyze.add_argument("--engine", help="Path to the Stockfish binary")
    analyze.add_argument("--user", help="Your username, to mark your own mistakes")
    analyze.add_argument("--json", action="store_true", help="Print the full analysis as JSON")
    analyze.set_defaults(func=_cmd_analyze)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (PGNError, EngineNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
