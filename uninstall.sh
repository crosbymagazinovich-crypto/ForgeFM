#!/bin/sh
# Remove program launchers only. Never delete the user's configuration.
set -eu
BASE="${XDG_DATA_HOME:-$HOME/.local/share}/forgefm"
BIN="$HOME/.local/bin/forgefm"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/forgefm.desktop"
ICON="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/forgefm.svg"
if [ -f "$BIN" ] && grep -Fq "$BASE/launch.py" "$BIN"; then
    rm -- "$BIN"
    printf 'Удалён лаунчер: %s\n' "$BIN"
fi
if [ -f "$DESKTOP" ] && grep -Fq 'Name=ForgeFM M/07' "$DESKTOP"; then
    rm -- "$DESKTOP"
    printf 'Удалён ярлык: %s\n' "$DESKTOP"
    if [ -f "$ICON" ] && grep -q 'ForgeFM M/07' "$ICON"; then
        rm -- "$ICON"
    fi
fi
printf 'Исходники и конфигурация сохранены: %s и ~/.config/forgefm\n' "$BASE"
