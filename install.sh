#!/bin/sh
# Per-user GTK4 desktop installation, no root access needed.
set -eu
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BASE="${XDG_DATA_HOME:-$HOME/.local/share}/forgefm"
BINDIR="$HOME/.local/bin"
APPDIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONDIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
PYTHON="${FORGEFM_PYTHON:-python3}"
if [ "$(id -u)" -eq 0 ] && [ "${FORGEFM_ALLOW_ROOT:-0}" != "1" ]; then
    printf '%s\n' 'Не запускай установщик от root / через sudo. Для Portage см. overlay/README.md.' >&2
    exit 1
fi
if [ "${FORGEFM_SKIP_GUI_CHECK:-0}" != "1" ]; then
    if ! "$PYTHON" -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk' >/dev/null 2>&1; then
        printf '%s\n' 'Для графической версии ForgeFM требуется GTK4 + PyGObject для выбранного python3.' >&2
        printf '%s\n' 'Gentoo: sudo emerge --ask gui-libs/gtk dev-python/pygobject' >&2
        printf '%s\n' 'Проверка: python3 -c '\''import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk; print("GTK4 OK")'\''' >&2
        exit 2
    fi
fi
mkdir -p "$BASE" "$BINDIR" "$APPDIR" "$ICONDIR"
for name in core.py presentation.py launch.py gui_gtk.py tui.py forgefm.py; do
    if [ -L "$BASE/$name" ]; then
        printf 'Отказ от перезаписи символической ссылки: %s\n' "$BASE/$name" >&2
        exit 1
    fi
done
for path in "$BINDIR/forgefm" "$APPDIR/forgefm.desktop" "$ICONDIR/forgefm.svg"; do
    if [ -L "$path" ]; then
        printf 'Отказ от перезаписи символической ссылки: %s\n' "$path" >&2
        exit 1
    fi
done
if [ -f "$BASE/launch.py" ] && [ ! -e "$BASE/launch.py.before-gtk4" ]; then
    cp "$BASE/launch.py" "$BASE/launch.py.before-gtk4"
fi
for file in core.py presentation.py launch.py gui_gtk.py tui.py forgefm.py; do
    install -m 644 "$SOURCE/$file" "$BASE/$file"
done
# Shell launcher keeps a stable app entry point for desktop shells.
cat > "$BINDIR/forgefm" <<EOF
#!/bin/sh
exec "$PYTHON" "$BASE/launch.py" "\$@"
EOF
chmod 755 "$BINDIR/forgefm"
# Freedesktop desktop entry uses the absolute path, not the shell PATH.
cat > "$APPDIR/forgefm.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=ForgeFM M/07
Name[ru]=ForgeFM M/07 — файлы
Comment=Single-window file manager with recursive directory sizes
Comment[ru]=Однооконный файловый менеджер с подсчётом размеров папок
Exec=$BINDIR/forgefm %f
TryExec=$BINDIR/forgefm
Icon=forgefm
Terminal=false
Categories=System;FileManager;Utility;
StartupNotify=true
EOF
chmod 644 "$APPDIR/forgefm.desktop"
install -m 644 "$SOURCE/assets/forgefm.svg" "$ICONDIR/forgefm.svg"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPDIR" >/dev/null 2>&1 || true
fi
printf '\n%s\n' 'ForgeFM M/07 v0.4.1 установлен как отдельное графическое приложение.'
printf 'Меню приложений: ForgeFM M/07    Запуск: %s/forgefm\n' "$BINDIR"
printf '%s\n' 'Старый TUI: forgefm --tui   Старый Tkinter GUI: forgefm --legacy-gui'
