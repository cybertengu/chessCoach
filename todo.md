# chessCoach TODO

Goal: find the mistakes that keep repeating across my games, then practice them until they stick.

- [x] **1. Consistent, faster analysis**
  - Same game → same verdicts every run: single-threaded Stockfish with a fixed node budget instead of a time cap.
  - Run several engine processes in parallel so a full game still takes ~10–20 s.
  - Cache results per game (hash of the moves) so re-opening a game is instant.
- [x] **2. Mistake database**
  - Store every flagged position (FEN, move played, best move, win % lost, game link, date, time control, clock left) in a local SQLite file.
  - Record retry outcomes too (solved / revealed / attempts).
- [x] **3. Pattern tags**
  - Rule-based tags with python-chess: hung piece, missed capture, missed fork/pin/skewer, back-rank, missed mate, allowed mate.
  - Context tags: opening (move ≤ 12), time trouble (clock < 10% of the start time), already winning/losing.
  - Optional, not done yet: a local LLM (Ollama) writes a short explanation from the engine facts, never judging moves itself.
- [x] **4. Batch analysis + "my top mistakes" report**
  - Analyze my last N Chess.com games (UsagiTengu) in one go.
  - Show counts by tag, phase and time control, trend over time, and links to example positions.
- [x] **5. Practice deck (spaced repetition)**
  - Turn my own mistake positions into puzzles that come back on a schedule (Leitner boxes: 1, 3, 7, 14, 30, 60, 120 days).
  - Same spoiler-free retry flow; failed puzzles come back sooner.
  - Optional, not done yet: a setting for the 10 new cards a day.

## Visual polish

Process: mock up 2–3 style directions first (e.g. Lichess-like, GNOME/Adwaita native,
dark & minimal), pick one, then build it as a stack of small PRs checked with screenshots.
Stay on Qt Widgets; no QML/GTK rewrite.

- [ ] **6. Theme**
  - Light/dark mode that follows the Fedora system setting; one stylesheet with color tokens.
  - Rounded panels, consistent spacing, clear hover/focus states.
  - Toolbar icons from the system theme (Adwaita).
- [ ] **7. Board & pieces**
  - Board color themes (brown / blue / green); coordinates outside the board.
  - Vertical eval bar beside the board.
  - Animated piece moves; optional move / capture / "solved" sounds.
  - Extra piece sets, only ones whose license allows it.
- [ ] **8. Right-hand panel**
  - Mistakes as cards: severity stripe, move, win % drop, ✓ / 👁 badges.
  - Big puzzle feedback banner ("✓ Good move!" / "✗ Still a blunder") plus a brief board flash.
  - Lichess-style move list, tidier game header, accuracy as a small chart.
- [ ] **9. Polish**
  - "Thinking…" overlay on the board while Stockfish works.
  - Start screen with big Open / Paste / Chess.com buttons.
  - "Analyze my last N games" in the app (like `chesscoach-cli batch`), with a progress bar
    and a Stop button.
  - Remember window size and splitter layout.
