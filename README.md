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
