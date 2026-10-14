#!/usr/bin/env python3
"""ForgeFM M/07 v0.4.1: GTK4 desktop app by default; TUI available."""
import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="ForgeFM M/07 — независимое приложение для Gentoo Linux")
    parser.add_argument("paths", nargs="*", help="Стартовая папка (две — только для --tui)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--tui", action="store_true", help="Предыдущий терминальный режим с двумя панелями")
    modes.add_argument("--gui", action="store_true", help="Новый графический режим GTK4 (по умолчанию)")
    modes.add_argument("--legacy-gui", action="store_true", help="Предыдущий Tkinter GUI")
    parser.add_argument("--demo", action="store_true", help="Показать TUI HUD без входа в интерактивный режим")
    parser.add_argument("--version", action="version", version="ForgeFM M/07 v0.4.1")
    args = parser.parse_args()
    if len(args.paths) > (2 if args.tui or args.demo else 1):
        parser.error("В GUI допустим один путь; два пути — в терминальном режиме")
    paths = [Path(p).expanduser() for p in args.paths]
    if args.tui or args.demo:
        from tui import ForgeTUI
        app = ForgeTUI(*paths)
        if args.demo:
            print(app.draw())
        else:
            try:
                app.run()
            except (OSError, RuntimeError) as exc:
                parser.error(str(exc))
    elif args.legacy_gui:
        try:
            from forgefm import ForgeFM
            from tkinter import TclError
        except ImportError as exc:
            parser.error(f"Предыдущий GUI требует tkinter: {exc}")
        try:
            app = ForgeFM(*paths)
            app.root.mainloop()
        except TclError as exc:
            parser.error(f"Нет графической сессии: {exc}")
    else:
        try:
            from gui_gtk import main as gtk_main
        except (ImportError, ValueError) as exc:
            parser.exit(2, "ForgeFM M/07: не найдены GTK4 / PyGObject.\n"
                           "Для Gentoo: sudo emerge --ask gui-libs/gtk dev-python/pygobject\n"
                           f"Подробности: {exc}\n")
        try:
            sys.exit(gtk_main(paths[0] if paths else Path.home()))
        except RuntimeError as exc:
            parser.exit(2, f"Не удалось открыть графическое окно: {exc}\n")


if __name__ == "__main__":
    main()
