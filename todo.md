# chessCoach TODO

Goal: find the mistakes that keep repeating across my games, then practise them until they stick.

- [ ] **1. Consistent, faster analysis**
  - Same game → same verdicts every run: single-threaded Stockfish with a fixed node budget instead of a time cap.
  - Run several engine processes in parallel so a full game still takes ~10–20 s.
  - Cache results per game (hash of the moves) so re-opening a game is instant.
- [ ] **2. Mistake database**
  - Store every flagged position (FEN, move played, best move, win % lost, game link, date, time control, clock left) in a local SQLite file.
  - Record retry outcomes too (solved / revealed / attempts).
- [ ] **3. Pattern tags**
  - Rule-based tags with python-chess: hung piece, missed capture, missed fork/pin/skewer, back-rank, missed mate, allowed mate.
  - Context tags: opening (move ≤ 12), time trouble (clock < 10% of the start time), already winning/losing.
  - Optional: a local LLM (Ollama) writes a short explanation from the engine facts, never judging moves itself.
- [ ] **4. Batch analysis + "my top mistakes" report**
  - Analyse my last N Chess.com games (UsagiTengu) in one go.
  - Show counts by tag, phase and time control, trend over time, and links to example positions.
- [ ] **5. Practice deck (spaced repetition)**
  - Turn my own mistake positions into puzzles that come back on a schedule (e.g. SM-2 / Leitner).
  - Same spoiler-free retry flow; failed puzzles come back sooner.
