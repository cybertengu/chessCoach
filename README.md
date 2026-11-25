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

```bash
.venv/bin/chesscoach            # or: .venv/bin/chesscoach my_game.pgn
```

Load a game from a `.pgn` file (Ctrl+O), pasted text (Ctrl+Shift+V), Chess.com
(Ctrl+I) or by dropping a file on the window. Stockfish analyses it in the
background. You then get:

- an evaluation graph (click it to jump to any move),
- the move list coloured by inaccuracy / mistake / blunder,
- **Your mistakes**: your errors only. Select one to see the position and the
  engine's best move. If your Chess.com username doesn't match either player,
  the app asks which side you played.

Use ← / → / Home / End to step through the game and F to flip the board.
