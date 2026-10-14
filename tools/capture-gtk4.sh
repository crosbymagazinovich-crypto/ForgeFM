#!/usr/bin/env bash
# Снимок РЕАЛЬНОГО графического ForgeFM (GTK4) в Gentoo / Plasma / GNOME.
# Запускайте из корня своего Git-репозитория ForgeFM.
set -euo pipefail
out="${1:-docs/screenshots/forgefm-gtk4.png}"
mkdir -p "$(dirname "$out")"
if [[ -z "${WAYLAND_DISPLAY:-}" && -z "${DISPLAY:-}" ]]; then
    echo "Ошибка: необходима графическая сессия Wayland или X11" >&2
    exit 2
fi
printf '%s\n' "Откройте ForgeFM и переведите фокус на его окно. Снимок через 5 секунд…"
sleep 5
if command -v spectacle >/dev/null 2>&1; then
    # KDE Plasma (Wayland и X11).
    spectacle -b -a -n -o "$out"
elif [[ "${XDG_SESSION_TYPE:-}" == x11 ]] && command -v scrot >/dev/null 2>&1; then
    scrot -u "$out"
elif command -v gnome-screenshot >/dev/null 2>&1; then
    gnome-screenshot -w -f "$out"
elif command -v grim >/dev/null 2>&1 && command -v slurp >/dev/null 2>&1; then
    grim -g "$(slurp)" "$out"
else
    echo "Нет доступного инструмента захвата. Для KDE установите kde-apps/spectacle." >&2
    exit 2
fi
if [[ ! -s "$out" ]]; then
    echo "Не удалось создать снимок: $out" >&2
    exit 1
fi
printf 'Готово: %s\n' "$out"
