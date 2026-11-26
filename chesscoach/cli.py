"""Command-line interface: ``chesscoach-cli analyze game.pgn``."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import sys

import chess

from .analysis import DEFAULT_NODES, GameAnalysis, MoveAnalysis, reply_line
from .batch import GameOutcome, GameStatus, analyze_games
from .cache import AnalysisCache, analyze_with_cache
from .chesscom import ChessComError, recent_games
from .classify import judge_game, player_color, side_accuracy
from .db import DatabaseError, MistakeDB, parse_time_control, refutation_line
from .engine import EngineNotFoundError, open_engine
from .pgn import PGNError, read_game_file
from .report import Filters, Period, Report, ReportMistake, TimeClass, build_report
from .tags import tag_mistake


def _plural(n: int, word: str) -> str:
    if n == 1:
        return f"1 {word}"
    return f"{n} {word[:-1]}ies" if word.endswith("y") else f"{n} {word}s"


def _print_table(result: GameAnalysis, username: str | None) -> None:
    judgements = judge_game(result)
    mine = player_color(result.headers, username)
    base_seconds, _ = parse_time_control(result.headers.get("TimeControl"))
    for j in judgements:
        m = j.move
        label = f"{m.label}{j.judgement.symbol}"
        note = ""
        if j.judgement.is_error:
            loss = f"(-{j.win_loss:.0f}%)"
            note = f"{j.judgement.value:<10} {loss:<6}  best: {m.best_move_san or '-':<7}"
            tags = tag_mistake(m, refutation_line(result, m.ply), base_seconds)
            if tags:
                note += "  [" + ", ".join(t.label for t in tags) + "]"
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
        print(f"\rAnalyzing position {done}/{total}", end="", file=sys.stderr, flush=True)

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

    mine = player_color(result.headers, args.user)
    if mine is not None and not args.no_save:
        db = MistakeDB()
        recorded = db.record_game(result, judge_game(result), mine, pgn=str(game))
        print(
            f"Saved {_plural(recorded.added, 'new mistake')} to {db.path}"
            f" ({len(recorded.mistake_ids)} stored for this game)",
            file=sys.stderr,
        )

    if args.json:
        json.dump(result.to_dict(), sys.stdout, indent=2)
        print()
    else:
        _print_table(result, args.user)
    return 0


def _cmd_tag(args: argparse.Namespace) -> int:
    db = MistakeDB()
    with contextlib.ExitStack() as stack:
        engine = None

        def find_reply(move: MoveAnalysis, nodes: int | None) -> list[str]:
            nonlocal engine
            if engine is None:  # only start Stockfish if a reply line is missing
                engine = stack.enter_context(open_engine(args.engine))
            return reply_line(engine, move, nodes)

        def progress(done: int, total: int) -> None:
            print(f"\rTagging mistake {done}/{total}", end="", file=sys.stderr, flush=True)

        count = db.retag(None if args.no_engine else find_reply, progress)
    if count:
        print(file=sys.stderr)
    print(f"Tagged {_plural(count, 'mistake')} in {db.path}")
    counts = db.tag_counts()
    if counts:
        print()
        width = max(len(t.label) for t in counts)
        for tag, n in counts.items():
            print(f"  {tag.label:<{width}}  {n}")
    return 0


def _describe_outcome(outcome: GameOutcome, username: str) -> str:
    game = outcome.game.describe(username)
    if outcome.status is GameStatus.SKIPPED:
        return f"{game}: already saved"
    if outcome.status is GameStatus.FAILED:
        return f"{game}: FAILED: {outcome.error}"
    return f"{game}: {_plural(outcome.mistakes, 'mistake')} ({outcome.added} new)"


def _cmd_batch(args: argparse.Namespace) -> int:
    print(f"Fetching the last {args.count} games of {args.username}...", file=sys.stderr)
    games = recent_games(args.username, count=args.count)
    if not games:
        print("No games found.")
        return 0
    width = len(str(len(games)))
    live = sys.stderr.isatty()  # a progress line that rewrites itself

    def on_start(index: int, total: int, game) -> None:
        if live:
            print(f"[{index:>{width}}/{total}] {game.describe(args.username)}...",
                  end="", file=sys.stderr, flush=True)

    def on_progress(done: int, total: int) -> None:
        if live:
            print(f"\r\033[K  position {done}/{total}", end="", file=sys.stderr, flush=True)

    def on_done(index: int, total: int, outcome: GameOutcome) -> None:
        if live:
            print("\r\033[K", end="", file=sys.stderr, flush=True)
        print(f"[{index:>{width}}/{total}] {_describe_outcome(outcome, args.username)}", flush=True)

    result = analyze_games(
        games,
        args.username,
        nodes=args.nodes,
        engine_count=args.engines,
        engine_path=args.engine,
        redo=args.redo,
        on_start=on_start,
        on_progress=on_progress,
        on_done=on_done,
    )
    if live and result.stopped:
        print("\r\033[K", end="", file=sys.stderr, flush=True)
    saved = result.count(GameStatus.SAVED)
    skipped = result.count(GameStatus.SKIPPED)
    print()
    print(
        f"Analyzed {_plural(saved, 'game')} ({_plural(result.added, 'new mistake')}), "
        f"{skipped} already saved, {len(result.failed)} failed."
    )
    if result.failed:
        print("Failed:")
        for outcome in result.failed:
            print(f"  {outcome.game.url}  {outcome.error}")
    if result.stopped:
        done = len(result.outcomes)
        print(f"Stopped after {done} of {len(games)} games; run the same command to continue.")
        return 130
    return 1 if result.failed else 0


def _example_line(m: ReportMistake) -> str:
    date = m.date.isoformat() if m.date else "?"
    where = m.link or "(no link)"
    return f"    {date}  vs {m.opponent:<20} {m.move_label:<12} {m.judgement:<10} -{m.win_loss:.0f}%  {where}"


def _print_report(report: Report) -> None:
    if not report.games:
        if report.filters != Filters():
            print("No games match these filters.")
        else:
            print("No games in your mistake database yet. "
                  "Run `chesscoach-cli batch <username>` first.")
        return
    span = ""
    if report.first_date and report.last_date:
        span = f", {report.first_date} to {report.last_date}"
    total = len(report.mistakes)
    print(f"My top mistakes: {_plural(report.games, 'game')}{span}")
    j = report.by_judgement
    print(
        f"{_plural(total, 'mistake')} ({report.per_game:.1f} per game): "
        f"{_plural(j['blunder'], 'blunder')}, {_plural(j['mistake'], 'mistake')}, "
        f"{_plural(j['inaccuracy'], 'inaccuracy')}"
    )
    if total:
        print(
            f"{report.no_pattern} of them ({100 * report.no_pattern / total:.0f}%) match no "
            "pattern; the phase table shows where they happen."
        )

    if report.tags:
        print()
        print(f"{'By pattern':<26}{'Count':>6}{'Share':>7}{'Avg loss':>10}{'Done':>9}")
        group = None
        for row in report.tags:
            if row.tag.group is not group:
                group = row.tag.group
                print(f"  {group.value.replace('_', ' ').capitalize()}")
            print(
                f"    {row.tag.label:<22}{row.count:>6}{row.share:>6.0f}%{row.avg_loss:>9.0f}%"
                f"{f'{row.done}/{row.count}':>9}"
            )

        print("  Done: solved or revealed in a retry.")

    print()
    print(f"{'By phase':<16}{'Count':>6}{'Blunder':>9}{'Mistake':>9}{'Inacc.':>8}"
          f"{'No pattern':>12}{'Avg loss':>10}")
    for row in report.phases:
        b = row.by_judgement
        print(
            f"  {row.phase.label:<14}{row.count:>6}{b['blunder']:>9}{b['mistake']:>9}"
            f"{b['inaccuracy']:>8}{row.no_pattern:>12}{row.avg_loss:>9.0f}%"
        )

    print()
    print(f"{'By time control':<18}{'Games':>6}{'Mistakes/game':>15}{'Blunders/game':>15}")
    for row in report.time_classes:
        print(
            f"  {row.time_class.label:<16}{row.games:>6}{row.per_game:>15.1f}"
            f"{row.blunders_per_game:>15.1f}"
        )

    if report.trend:
        print()
        print(f"{'Trend by ' + report.period.value:<18}{'Games':>6}{'Mistakes/game':>15}"
              f"{'Blunders/game':>15}  Top patterns")
        for row in report.trend:
            top = ", ".join(f"{tag.label} {n}" for tag, n in row.top_patterns) or "-"
            print(
                f"  {row.period:<16}{row.games:>6}{row.per_game:>15.1f}"
                f"{row.blunders_per_game:>15.1f}  {top}"
            )

    sections: list[tuple[str, list[ReportMistake], int]] = []
    for row in report.tags:
        sections.append((row.tag.label, row.examples, row.hidden))
    for row in report.phases:
        if row.no_pattern_examples:
            sections.append((f"No pattern found: {row.phase.label.lower()}", row.no_pattern_examples, 0))
    if any(examples or hidden for _, examples, hidden in sections):
        print()
        print("Examples (biggest drop in win % first; the best move is never shown)")
        for title, examples, hidden in sections:
            print(f"  {title}")
            for m in examples:
                print(_example_line(m))
            if hidden:
                print(
                    f"    {_plural(hidden, 'position')} not listed: listing them here would hint at "
                    "the answer. Retry your mistakes in the app to unlock them."
                )


def _cmd_report(args: argparse.Namespace) -> int:
    db = MistakeDB()
    filters = Filters(
        since=args.since,
        last_games=args.last,
        time_class=TimeClass(args.time_control) if args.time_control else None,
    )
    report = build_report(db, filters, period=Period(args.by), examples=args.examples)
    if report.stale_tags:
        # Tagging from what is stored is quick; `chesscoach-cli tag` can add
        # the tags that need a Stockfish search.
        count = db.retag()
        print(f"Updated the tags of {_plural(count, 'mistake')} tagged by older rules.\n",
              file=sys.stderr)
        report = build_report(db, filters, period=Period(args.by), examples=args.examples)
    _print_report(report)
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

    analyze = sub.add_parser("analyze", help="Analyze a PGN file with Stockfish")
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
        "--no-cache", action="store_true", help="Re-analyze even if this game was analyzed before"
    )
    analyze.add_argument(
        "--user", help="Your username, to mark your own mistakes and save them to the mistake database"
    )
    analyze.add_argument(
        "--no-save", action="store_true", help="Don't save your mistakes to the mistake database"
    )
    analyze.add_argument("--json", action="store_true", help="Print the full analysis as JSON")
    analyze.set_defaults(func=_cmd_analyze)

    tag = sub.add_parser(
        "tag",
        help="Add pattern tags to the mistakes in your mistake database",
        description=(
            "Tag every stored mistake that has no tags yet or was tagged by older rules, "
            "then show how often each tag appears. Mistakes saved by older versions of "
            "chessCoach need one Stockfish search each for the opponent's best reply."
        ),
    )
    tag.add_argument("--engine", help="Path to the Stockfish binary")
    tag.add_argument(
        "--no-engine",
        action="store_true",
        help="Don't run Stockfish; tag from what is stored (some tags may be missed)",
    )
    tag.set_defaults(func=_cmd_tag)

    batch = sub.add_parser(
        "batch",
        help="Analyze your recent Chess.com games and save your mistakes",
        description=(
            "Analyze your last games from Chess.com one after another and save your "
            "mistakes (with pattern tags) to the mistake database. Games already saved "
            "are skipped, so you can stop with Ctrl-C and run it again to continue."
        ),
    )
    batch.add_argument("username", help="Your Chess.com username")
    batch.add_argument("--count", type=int, default=50, help="How many games (default 50)")
    batch.add_argument(
        "--nodes",
        type=int,
        default=DEFAULT_NODES,
        help=f"Nodes searched per position (default {DEFAULT_NODES:,})",
    )
    batch.add_argument("--engine", help="Path to the Stockfish binary")
    batch.add_argument(
        "--engines",
        type=int,
        help="Number of Stockfish processes to run in parallel (default: one per CPU core)",
    )
    batch.add_argument(
        "--redo", action="store_true", help="Analyze and save games again even if already saved"
    )
    batch.set_defaults(func=_cmd_batch)

    report = sub.add_parser(
        "report",
        help="Show your top mistakes: by pattern, phase, time control and month",
        description=(
            "Summarize the mistakes in your mistake database, with example positions. "
            "The best moves are never shown."
        ),
    )
    report.add_argument(
        "--since", type=dt.date.fromisoformat, metavar="YYYY-MM-DD", help="Only games since this date"
    )
    report.add_argument("--last", type=int, metavar="N", help="Only your last N games")
    report.add_argument(
        "--time-control",
        choices=[t.value for t in TimeClass if t is not TimeClass.UNKNOWN],
        help="Only games of this speed",
    )
    report.add_argument(
        "--by", choices=[p.value for p in Period], default="month", help="Trend period (default month)"
    )
    report.add_argument(
        "--examples", type=int, default=3, metavar="N", help="Example positions per row (default 3)"
    )
    report.set_defaults(func=_cmd_report)

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
    except (PGNError, EngineNotFoundError, ChessComError, DatabaseError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
