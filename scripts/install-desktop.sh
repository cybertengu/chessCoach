#!/usr/bin/env bash
# Install chessCoach into the Fedora (GNOME/KDE) app menu for the current user.
#
#   scripts/install-desktop.sh                # menu entry + icon
#   scripts/install-desktop.sh --default-pgn  # also make it the default app for .pgn files
#   scripts/install-desktop.sh --uninstall
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS="$DATA/applications"
ICONS="$DATA/icons/hicolor/scalable/apps"
DESKTOP="$APPS/chesscoach.desktop"

refresh() {
    command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true
    command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "$DATA/icons/hicolor" 2>/dev/null || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$DESKTOP" "$ICONS/chesscoach.svg"
    refresh
    echo "Removed chessCoach from the app menu."
    exit 0
fi

if ! command -v stockfish >/dev/null; then
    echo "Stockfish is not installed. Run: sudo dnf install stockfish" >&2
    exit 1
fi

VENV="$ROOT/.venv"
if [[ ! -x "$VENV/bin/chesscoach" ]]; then
    echo "Setting up the Python environment in $VENV…"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -e "$ROOT[gui]"
fi

mkdir -p "$APPS" "$ICONS"
install -m 644 "$ROOT/packaging/chesscoach.svg" "$ICONS/chesscoach.svg"
# Quote the Exec path per the desktop-entry spec in case the repo path has spaces.
sed "s|@EXEC@|\"$VENV/bin/chesscoach\"|" "$ROOT/packaging/chesscoach.desktop.in" > "$DESKTOP"
chmod 644 "$DESKTOP"
command -v desktop-file-validate >/dev/null && desktop-file-validate "$DESKTOP"

if [[ "${1:-}" == "--default-pgn" ]]; then
    xdg-mime default chesscoach.desktop application/vnd.chess-pgn application/x-chess-pgn
    echo "chessCoach is now the default app for .pgn files."
fi

refresh
echo "Installed. Look for \"chessCoach\" in your app menu, or right-click a .pgn file → Open With."
