# Credits

chessCoach is built on other people's work. Thank you!

## Used by the app

| Project | Author | Licence | How chessCoach uses it |
|---|---|---|---|
| [Stockfish](https://stockfishchess.org/) | The Stockfish developers | GPL-3.0-or-later | Chess engine. Not bundled: installed separately (`dnf install stockfish`) and run as its own program over the UCI protocol. |
| [python-chess](https://github.com/niklasf/python-chess) | Niklas Fiekas and contributors | GPL-3.0-or-later | Chess rules, PGN reading, engine communication, piece artwork. Installed by pip. |
| [Qt for Python (PySide6)](https://doc.qt.io/qtforpython-6/) | The Qt Company | LGPL-3.0-only (or GPL-2.0/3.0) | Desktop user interface. Installed by pip. |

## Artwork

- **Chess pieces** on the board, and the **knight in the app icon**
  (`packaging/chesscoach.svg`), are the "cburnett" set by
  **Colin M. L. Burnett**, taken from python-chess. The set is published on
  [Wikimedia Commons](https://commons.wikimedia.org/wiki/Category:SVG_chess_pieces)
  under a choice of GPL-2.0-or-later, BSD or GFDL. The app icon is a modified
  version (the knight placed on a board tile).

## Ideas and data

- **[Lichess](https://lichess.org/)** ([lila](https://github.com/lichess-org/lila), AGPL-3.0):
  the centipawn → win % formula, the inaccuracy / mistake / blunder thresholds and
  the accuracy formula follow Lichess's published approach. They were reimplemented
  here; no Lichess code is copied.
- **[Chess.com Published-Data API](https://www.chess.com/news/view/published-data-api)**:
  used to download your public games.

## Development only

- [pytest](https://pytest.org/) (MIT) for the test suite.
