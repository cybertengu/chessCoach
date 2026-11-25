# chessCoach
Analyze my games and try to help me improve.

chessCoach runs your games through Stockfish, flags your inaccuracies, mistakes
and blunders, and lets you replay each one from that position until you find a
better move.

## Setup (Fedora)

```bash
sudo dnf install stockfish
python3 -m venv .venv
.venv/bin/pip install -e '.[gui,dev]'
```

If Stockfish is installed somewhere unusual, point to it with
`export CHESSCOACH_STOCKFISH=/path/to/stockfish`.

## Running the tests

```bash
.venv/bin/pytest
```

## Command line

```bash
.venv/bin/chesscoach-cli analyze my_game.pgn --user UsagiTengu   # flags your mistakes
.venv/bin/chesscoach-cli analyze my_game.pgn --json   # full analysis as JSON
```

Each position is searched to depth 16 or for 0.25 s, whichever comes first
(`--depth`, `--max-seconds`).

Moves are judged like on Lichess: each position's evaluation is converted to a
win percentage, and a move that gives away at least 5 / 10 / 15 percentage
points versus the engine's best move is an inaccuracy (`?!`), mistake (`?`)
or blunder (`??`).

## Importing from Chess.com

```bash
.venv/bin/chesscoach-cli recent UsagiTengu --count 10              # list recent games
.venv/bin/chesscoach-cli recent UsagiTengu --count 10 --save games # also save .pgn files
```

This uses Chess.com's public API, so no login is needed. Chess variants are skipped.

## Desktop app

Add chessCoach to your Fedora app menu (this also sets up `.venv` if needed):

```bash
scripts/install-desktop.sh                # app menu entry + icon
scripts/install-desktop.sh --default-pgn  # also open .pgn files with chessCoach on double-click
scripts/install-desktop.sh --uninstall
```

Or run it directly: `.venv/bin/chesscoach [my_game.pgn]`.

Save games from Chess.com or Lichess as `.pgn` files ("Download PGN" on the game page).

Load a game from a `.pgn` file (Ctrl+O), pasted text (Ctrl+Shift+V), Chess.com
(Ctrl+I) or by dropping a file on the window. Stockfish analyses it in the
background. You then get:

- an evaluation graph (click it to jump to any move),
- the move list coloured by inaccuracy / mistake / blunder,
- **Your mistakes**: your errors only. Select one to see the position and the
  engine's best move. If your Chess.com username doesn't match either player,
  the app asks which side you played.

### Retry your mistakes

Select one of your mistakes and press **Try a better move** (T), or
double-click the mistake. The board rewinds to the position before you went
wrong. Play a move by clicking or dragging:

- ✓ **Good/best move**: the mistake is ticked off in the list. Use **Next mistake** to continue.
- ✗ **Still an error**: you see how much it costs and the opponent's best reply
  (red arrow). Press **Try again**.
- **Hint** highlights the piece to move; **Show answer** shows the engine's line.

Use ← / → / Home / End to step through the game and F to flip the board.
