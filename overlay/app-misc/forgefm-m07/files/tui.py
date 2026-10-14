#!/usr/bin/env python3
"""SYNCHROFRAME-inspired, dependency-free truecolor terminal interface for ForgeFM.

TUI is an original implementation and is not part of Synchroframe/Zsh.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import getpass
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import termios
import tty
import unicodedata

from core import (create_folder, human_size, normalize_path, read_config,
                  rename_item, scan_directory, transfer_many, trash_many,
                  write_config)

VERSION = "0.2.0"

# Red accents mirror ForgeFM GTK; palette is not imported from the Konsole profile.
# Legacy token names mint/cyan remain for internal compatibility.
COLORS = {
    "background": "#140f17", "panel": "#1c1720", "raised": "#29212b",
    "muted": "#8e8496", "text": "#e6dce8", "mint": "#ff5865",
    "cyan": "#ff8992", "purple": "#c2a4fd", "gold": "#eab77f",
    "red": "#ff7a9b", "selected": "#403342", "line": "#514356",
}


def width_of(ch: str) -> int:
    """Sufficient screen width approximation without requiring wcwidth."""
    if unicodedata.combining(ch) or unicodedata.category(ch) in {"Cf", "Cc"}:
        return 0
    return 2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1


def text_width(s: str) -> int:
    return sum(width_of(c) for c in s)


def fitted(text: str, width: int, pad: bool = False) -> str:
    """Clip text without splitting a wide Unicode character."""
    if width <= 0:
        return ""
    used, out = 0, []
    if text_width(text) > width and width > 1:
        width -= 1
        suffix = "…"
    else:
        suffix = ""
    for c in text:
        w = width_of(c)
        if w == 0:
            continue
        if used + w > width:
            break
        out.append(c)
        used += w
    result = "".join(out) + suffix
    if pad:
        result += " " * max(0, width - used)
    return result


def rgb(color: str, foreground=True) -> str:
    r, g, b = (int(color[n:n+2], 16) for n in (1, 3, 5))
    return f"\x1b[{38 if foreground else 48};2;{r};{g};{b}m"


class Surface:
    def __init__(self, columns: int, rows: int):
        self.cols, self.rows = columns, rows
        self.cells = [[(" ", "text", "background", False) for _ in range(columns)] for _ in range(rows)]

    def put(self, x: int, y: int, text: str, *, fg="text", bg="background", bold=False):
        if y < 0 or y >= self.rows:
            return
        for char in text:
            count = width_of(char)
            if count == 0:
                continue
            if x >= self.cols:
                break
            if x >= 0 and x + count <= self.cols:
                self.cells[y][x] = (char, fg, bg, bold)
                if count == 2:
                    self.cells[y][x+1] = ("", fg, bg, bold)
            x += count

    def fill(self, x: int, y: int, length: int, *, bg="panel"):
        self.put(x, y, " " * max(0, length), bg=bg)

    def border(self, x: int, y: int, width: int, height: int, *, fg="line"):
        if width < 3 or height < 3:
            return
        self.put(x, y, "┌" + "─" * (width-2) + "┐", fg=fg)
        for n in range(y+1, y+height-1):
            self.put(x, n, "│", fg=fg)
            self.put(x+width-1, n, "│", fg=fg)
        self.put(x, y+height-1, "└" + "─" * (width-2) + "┘", fg=fg)

    def render(self) -> str:
        lines = ["\x1b[H"]
        for rowno, row in enumerate(self.cells):
            lines.append(f"\x1b[{rowno+1};1H")
            last = None
            for char, fg, bg, bold in row:
                if not char:
                    continue
                state = (fg, bg, bold)
                if state != last:
                    lines.append("\x1b[0m" + rgb(COLORS[fg]) + rgb(COLORS[bg], False) + ("\x1b[1m" if bold else ""))
                    last = state
                lines.append(char)
            lines.append("\x1b[0m")
        return "".join(lines)


@dataclass
class Pane:
    directory: Path
    entries: list = field(default_factory=list)
    index: int = 0
    scroll: int = 0
    marks: set[Path] = field(default_factory=set)
    filter: str = ""
    error: str = ""

    def refresh(self) -> None:
        try:
            self.entries = scan_directory(self.directory)
            self.error = ""
        except OSError as exc:
            self.entries = []
            self.error = str(exc)

    def visible(self, hidden: bool) -> list:
        q = self.filter.casefold()
        return sorted(
            [v for v in self.entries if (hidden or not v.name.startswith("."))
             and (not q or q in v.name.casefold())],
            key=lambda v: (not v.is_dir, v.name.casefold()),
        )

    def selected(self, hidden: bool):
        v = self.visible(hidden)
        if not v:
            return None
        self.index = min(max(0, self.index), len(v)-1)
        return v[self.index]

    def paths(self, hidden: bool) -> list[Path]:
        if self.marks:
            return [v.path for v in self.visible(True) if v.path in self.marks]
        chosen = self.selected(hidden)
        return [chosen.path] if chosen else []

    def navigate(self, path: Path) -> None:
        target = normalize_path(path)
        if not target.is_dir():
            raise NotADirectoryError(str(target))
        scan = scan_directory(target)  # fail without altering previous view
        self.directory, self.entries = target, scan
        self.marks.clear()
        self.index = self.scroll = 0
        self.filter = self.error = ""


def git_branch(path: Path) -> str:
    """Non-blocking and never executes Git; only resolves .git/HEAD."""
    for parent in (path, *path.parents):
        head = parent / ".git" / "HEAD"
        if head.is_file():
            try:
                ref = head.read_text(encoding="utf-8")[:200].strip()
                return ref.split("/")[-1] if ref.startswith("ref: refs/") else ref[:8]
            except OSError:
                return ""
    return ""


class Input:
    CODES = {
        b"\x1b[A": "up", b"\x1b[B": "down", b"\x1b[C": "right", b"\x1b[D": "left",
        b"\x1bOA": "up", b"\x1bOB": "down", b"\x1bOC": "right", b"\x1bOD": "left",
        b"\x1b[5~": "pgup", b"\x1b[6~": "pgdown", b"\x1b[H": "home", b"\x1b[F": "end",
        b"\x1b[1~": "home", b"\x1b[4~": "end", b"\x1bOH": "home", b"\x1bOF": "end",
        b"\x1b[3~": "delete", b"\x1bOQ": "f2", b"\x1b[12~": "f2",
        b"\x1bOR": "f3", b"\x1b[13~": "f3", b"\x1bOS": "f4", b"\x1b[14~": "f4",
        b"\x1b[15~": "f5", b"\x1b[17~": "f6", b"\x1b[18~": "f7", b"\x1b[19~": "f8",
        b"\x1b[21~": "f10", b"\x1bOP": "f1", b"\x1b[11~": "f1",
        b"\x1b[1;3D": "back", b"\x1b[1;3C": "forward",
    }

    def __init__(self):
        self.buffer = b""

    def next(self, timeout: float = 0.3) -> str | None:
        if not self.buffer:
            r, _, _ = select.select([sys.stdin], [], [], timeout)
            if not r:
                return None
            self.buffer += os.read(sys.stdin.fileno(), 4096)
        for sequence, name in sorted(self.CODES.items(), key=lambda t: -len(t[0])):
            if self.buffer.startswith(sequence):
                self.buffer = self.buffer[len(sequence):]
                return name
        if self.buffer.startswith(b"\x1b"):
            r, _, _ = select.select([sys.stdin], [], [], 0.025)
            if r:
                self.buffer += os.read(sys.stdin.fileno(), 128)
                for seq, name in sorted(self.CODES.items(), key=lambda t: -len(t[0])):
                    if self.buffer.startswith(seq):
                        self.buffer = self.buffer[len(seq):]
                        return name
            # Unrecognized sequences are swallowed to prevent accidental actions.
            if len(self.buffer) > 1 and self.buffer[1:2] in (b"[", b"O"):
                end = next((i for i in range(2, len(self.buffer)) if 64 <= self.buffer[i] <= 126), len(self.buffer)-1)
                self.buffer = self.buffer[end+1:]
                return None
            self.buffer = self.buffer[1:]
            return "esc"
        b = self.buffer[0]
        if b < 128:
            self.buffer = self.buffer[1:]
            return {9:"tab", 10:"enter", 13:"enter", 127:"backspace", 8:"backspace", 3:"ctrl-c", 12:"ctrl-l"}.get(b, chr(b))
        count = 2 if b < 224 else 3 if b < 240 else 4
        if len(self.buffer) < count:
            r, _, _ = select.select([sys.stdin], [], [], 0.1)
            if r:
                self.buffer += os.read(sys.stdin.fileno(), 128)
        segment = self.buffer[:count]
        self.buffer = self.buffer[len(segment):]
        return segment.decode("utf-8", errors="replace")


class ForgeTUI:
    def __init__(self, left=None, right=None):
        cfg = read_config()
        lpath = normalize_path(left or cfg["start_left"])
        rpath = normalize_path(right or cfg["start_right"])
        self.panes = [Pane(lpath if lpath.is_dir() else Path.home()),
                      Pane(rpath if rpath.is_dir() else Path.home())]
        for p in self.panes:
            p.refresh()
        self.active = 0
        self.config = cfg
        self.hidden = cfg["show_hidden"]
        self.message = "Система готова · F1 справка · Tab сменить панель"
        self.help = False
        self.preview = False
        self.input = Input()
        self.dialog: tuple[str, str] | None = None
        self.running = True
        self._last_size = (0, 0)

    @property
    def current(self) -> Pane:
        return self.panes[self.active]

    @property
    def other(self) -> Pane:
        return self.panes[1-self.active]

    def size(self):
        s = shutil.get_terminal_size(fallback=(100, 30))
        return s.columns, s.lines

    def draw(self) -> str:
        cols, rows = self.size()
        s = Surface(cols, rows)
        if cols < 62 or rows < 17:
            s.put(2, 2, fitted("FORGEFM M/07 • Увеличьте окно терминала (мин. 62×17)", cols-4), fg="mint")
            return s.render()
        s.fill(0, 0, cols, bg="raised")
        s.put(2, 0, "▰ M/07", fg="mint", bg="raised", bold=True)
        s.put(11, 0, fitted("▰▰▱ SYNCHROFRAME   //   FORGEFM", cols-32), fg="text", bg="raised", bold=True)
        right = "● ONLINE " + datetime.now().strftime("%H:%M")
        s.put(cols-text_width(right)-2, 0, right, fg="mint", bg="raised")
        cwd = self.current.directory
        branch = git_branch(cwd)
        user = getpass.getuser()
        core = f"◇ CORE   {user}@gentoo  {self._short_path(cwd)}" + (f"  ⌁{branch}" if branch else "")
        s.put(2, 2, fitted(core, cols-4), fg="cyan")
        s.put(2, 3, fitted("↯ DRIVE  ›  TWO-PANE FILE SYSTEM / NAVIGATION / OPERATIONS", cols-4), fg="purple")
        s.put(1, 4, "─" * (cols-2), fg="line")
        inset, gap = 2, 2
        each = (cols-2*inset-gap) // 2
        left_width = each
        right_x = inset+left_width+gap
        right_width = cols-inset-right_x
        bottom = rows-7
        self.draw_pane(s, self.panes[0], 0, inset, 5, left_width, bottom-5)
        self.draw_pane(s, self.panes[1], 1, right_x, 5, right_width, bottom-5)
        s.put(2, rows-6, fitted("◈ TELEMETRY  //  " + self._detail(), cols-4), fg="gold")
        s.put(2, rows-5, fitted("▰  " + self.message, cols-4), fg="red" if self.message.startswith("Ошибка") else "mint")
        s.put(1, rows-4, "─" * (cols-2), fg="line")
        s.fill(0, rows-3, cols, bg="raised")
        s.put(2, rows-3, fitted("F2 RENAME    F5 COPY    F6 MOVE    F7 MKDIR    F8 TRASH    F10 EXIT", cols-4), bg="raised", fg="mint", bold=True)
        s.put(2, rows-2, fitted("TAB PANEL  •  SPACE MARK  •  / FILTER  •  . HIDDEN  •  G PATH  •  P PREVIEW  •  ? HELP", cols-4), fg="muted")
        s.put(2, rows-1, fitted(f"◇ CORE :: FORGEFM v{VERSION}  /  без Tkinter  /  Esc отмена", cols-4), fg="purple")
        if self.help:
            self._help_overlay(s)
        if self.dialog:
            self._dialog_overlay(s)
        return s.render()

    def _short_path(self, p: Path) -> str:
        try:
            return "~" + ("/" + str(p.relative_to(Path.home())) if p != Path.home() else "")
        except ValueError:
            return str(p)

    def draw_pane(self, s: Surface, pane: Pane, side: int, x: int, y: int, w: int, h: int):
        if w < 10 or h < 6:
            return
        active = side == self.active
        s.border(x, y, w, h, fg="mint" if active else "line")
        s.put(x+2, y, f" {('01 // SOURCE' if side == 0 else '02 // TARGET')} ", fg="mint" if active else "muted")
        s.fill(x+1, y+1, w-2, bg="panel")
        s.put(x+2, y+1, fitted(self._short_path(pane.directory), w-4), fg="cyan", bg="panel")
        s.fill(x+1, y+2, w-2, bg="raised")
        s.put(x+2, y+2, fitted("NAME".ljust(max(4,w-17)) + "SIZE", w-4), fg="muted", bg="raised")
        visible = pane.visible(self.hidden)
        pane.index = min(max(pane.index, 0), max(0, len(visible)-1))
        height = h-5
        pane.scroll = max(0, min(pane.scroll, max(0, len(visible)-height)))
        if pane.index < pane.scroll:
            pane.scroll = pane.index
        if pane.index >= pane.scroll + height:
            pane.scroll = pane.index - height + 1
        for rowno in range(height):
            yy = y+3+rowno
            if yy >= y+h-2:
                break
            idx = pane.scroll+rowno
            chosen = active and idx == pane.index and idx < len(visible)
            bg = "selected" if chosen else "panel" if rowno % 2 == 0 else "background"
            s.fill(x+1, yy, w-2, bg=bg)
            if idx >= len(visible):
                continue
            v = visible[idx]
            marked = v.path in pane.marks
            icon = "▸" if v.is_dir else "↗" if v.is_link else "·"
            color = "cyan" if v.is_dir else "purple" if v.is_link else "text"
            s.put(x+2, yy, "◆" if marked else "›" if chosen else " ", fg="gold" if marked else "mint", bg=bg)
            s.put(x+4, yy, icon, fg=color, bg=bg)
            available = w-19
            s.put(x+6, yy, fitted(v.name, available), fg=color, bg=bg, bold=chosen)
            size = "<DIR>" if v.is_dir else human_size(v.size)
            s.put(x+w-3-min(10, text_width(size)), yy, fitted(size, 10), fg="muted", bg=bg)
        s.fill(x+1, y+h-2, w-2, bg="raised")
        detail = f" {len(visible)} объектов"
        if pane.filter:
            detail += f"  /{pane.filter}"
        if pane.marks:
            detail += f"  ◆{len(pane.marks)}"
        if pane.error:
            detail = "Ошибка: " + pane.error
        s.put(x+2, y+h-2, fitted(detail, w-4), fg="mint" if active else "muted", bg="raised")

    def _detail(self):
        entry = self.current.selected(self.hidden)
        if entry is None:
            return self._short_path(self.current.directory)
        size = "каталог" if entry.is_dir else human_size(entry.size)
        try:
            modified = datetime.fromtimestamp(entry.modified).strftime("%d.%m.%Y %H:%M")
        except (OverflowError, OSError, ValueError):
            modified = "?"
        if self.preview and not entry.is_dir:
            try:
                with entry.path.open("rb") as f:
                    chunk = f.read(512)
                snippet = chunk.decode("utf-8").replace("\r", " ").replace("\n", " ↵ ")
                if "\x00" not in snippet:
                    return f"PREVIEW  {entry.name}: {snippet[:110]}"
            except (OSError, UnicodeError):
                pass
        return f"{entry.name}  •  {size}  •  {modified}  •  {len(self.current.marks)} marked"

    def _help_overlay(self, s: Surface):
        cols, rows = s.cols, s.rows
        w = min(cols-8, 70)
        lines = [
            "  NAVIGATION", "  ↑ ↓  выбор файла     Enter → каталог/открыть",
            "  ← / Backspace  вверх   Tab сменить панель",
            "  PgUp/PgDn  прокрутка   G путь  / фильтр",
            "  .  скрытые файлы      Space  отметить файл",
            "  OPERATIONS", "  F2 rename    F5 copy    F6 move",
            "  F7 mkdir     F8 trash   P preview",
            "  R refresh    F10 / Q exit   ? help",
            "  Все опасные операции требуют подтверждения.",
            "  Esc закрыть справку."
        ]
        h = len(lines)+4
        x, y = (cols-w)//2, max(1, (rows-h)//2)
        s.border(x, y, w, h, fg="mint")
        for row in range(1, h-1):
            s.fill(x+1,y+row,w-2,bg="raised")
        s.put(x+2, y, " ◇ M/07 // HELP ", fg="mint")
        for i, line in enumerate(lines):
            s.put(x+2, y+2+i, fitted(line, w-4), fg="mint" if line.strip() in {"NAVIGATION", "OPERATIONS"} else "text", bg="raised")

    def _dialog_overlay(self, s: Surface):
        kind, prompt = self.dialog
        w = min(s.cols-8, max(40, min(72, text_width(prompt)+6)))
        x, y = (s.cols-w)//2, max(5, s.rows//2-2)
        s.border(x, y, w, 5, fg="gold")
        for r in range(1,4):
            s.fill(x+1,y+r,w-2,bg="raised")
        s.put(x+2,y," ↯ DRIVE // " + kind.upper() + " ",fg="gold")
        s.put(x+2,y+2,fitted(prompt,w-4),bg="raised",fg="text")

    def notify(self, msg: str):
        self.message = msg

    def refresh(self):
        for p in self.panes:
            p.refresh()

    def switch(self):
        self.active = 1-self.active
        self.notify("Активная панель: " + ("SOURCE" if self.active == 0 else "TARGET"))

    def down(self, offset):
        p = self.current
        p.index = min(max(0, len(p.visible(self.hidden))-1), max(0, p.index+offset))

    def open(self):
        p = self.current
        item = p.selected(self.hidden)
        if not item:
            return
        if item.is_dir:
            p.navigate(item.path)
            self.notify(f"Открыт каталог: {self._short_path(p.directory)}")
        else:
            opener = shutil.which("xdg-open")
            if opener:
                subprocess.Popen([opener, str(item.path)], cwd=str(p.directory),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                self.notify("Открывается: " + item.name)
            else:
                self.notify("Ошибка: не найден xdg-open (можно установить x11-misc/xdg-utils)")

    def up(self):
        self.current.navigate(self.current.directory.parent)

    def read_line(self, title: str, initial="") -> str | None:
        value = initial
        while True:
            self.dialog = (title, "> " + value + "█  [Enter OK / Esc отмена]")
            self.flush()
            key = self.input.next()
            if key in ("esc", "ctrl-c"):
                self.dialog = None
                return None
            if key == "enter":
                self.dialog = None
                return value
            if key == "backspace":
                value = value[:-1]
            elif key and len(key) == 1 and key.isprintable() and len(value) < 512:
                value += key

    def confirm(self, message: str) -> bool:
        self.dialog = ("confirm", message + "  [Y/N]")
        while True:
            self.flush()
            key = self.input.next()
            if key and key.lower() in ("y", "n") or key in ("esc", "ctrl-c"):
                self.dialog = None
                return key is not None and key.lower() == "y"

    def transfer(self, move: bool):
        paths = self.current.paths(self.hidden)
        if not paths:
            self.notify("Нечего переносить: выберите файл")
            return
        if move and not self.confirm(f"Переместить {len(paths)} → {self._short_path(self.other.directory)}?"):
            self.notify("Операция отменена")
            return
        destinations = transfer_many(paths, self.other.directory, move=move)
        self.current.marks.clear()
        self.refresh()
        self.notify(f"{'Перемещено' if move else 'Скопировано'}: {len(destinations)} объектов → {self._short_path(self.other.directory)}")

    def trash(self):
        paths = self.current.paths(self.hidden)
        if not paths:
            return
        if self.config.get("confirm_trash", True) and not self.confirm(f"Отправить в корзину {len(paths)} объектов?"):
            self.notify("Отправка в корзину отменена")
            return
        trash_many(paths)
        self.current.marks.clear()
        self.refresh()
        self.notify(f"В корзине: {len(paths)} объектов")

    def keypress(self, key: str):
        if self.help:
            self.help = False
            return
        if key in ("q", "f10", "ctrl-c"):
            self.running = False
        elif key == "tab":
            self.switch()
        elif key in ("up", "k"):
            self.down(-1)
        elif key in ("down", "j"):
            self.down(1)
        elif key == "pgup":
            self.down(-15)
        elif key == "pgdown":
            self.down(15)
        elif key == "home":
            self.current.index = 0
        elif key == "end":
            self.current.index = max(0, len(self.current.visible(self.hidden))-1)
        elif key in ("enter", "right", "l"):
            self.open()
        elif key in ("left", "backspace", "h"):
            self.up()
        elif key == " ":
            p = self.current
            item = p.selected(self.hidden)
            if item:
                if item.path in p.marks:
                    p.marks.remove(item.path)
                else:
                    p.marks.add(item.path)
                self.down(1)
        elif key in (".",):
            self.hidden = not self.hidden
            self.config["show_hidden"] = self.hidden
            write_config(self.config)
            self.notify("Скрытые файлы: " + ("ON" if self.hidden else "OFF"))
        elif key in ("/",):
            result = self.read_line("filter", self.current.filter)
            if result is not None:
                self.current.filter, self.current.index, self.current.scroll = result, 0, 0
        elif key in ("g", "ctrl-l"):
            value = self.read_line("go to path", str(self.current.directory))
            if value:
                target = Path(os.path.expanduser(value))
                if not target.is_absolute():
                    target = self.current.directory / target
                self.current.navigate(target)
        elif key == "f2":
            item = self.current.selected(self.hidden)
            if item:
                value = self.read_line("rename", item.name)
                if value is not None:
                    result = rename_item(item.path, value)
                    self.refresh()
                    self.notify("Переименовано: " + result.name)
        elif key == "f5":
            self.transfer(False)
        elif key == "f6":
            self.transfer(True)
        elif key == "f7":
            value = self.read_line("new directory")
            if value is not None:
                created = create_folder(self.current.directory, value)
                self.refresh()
                self.notify("Создан каталог: " + created.name)
        elif key in ("f8", "delete"):
            self.trash()
        elif key in ("?", "f1"):
            self.help = True
        elif key == "p":
            self.preview = not self.preview
            self.notify("PREVIEW " + ("ON" if self.preview else "OFF"))
        elif key == "r":
            self.refresh()
            self.notify("Панели обновлены")
        elif key == "esc":
            self.current.marks.clear()
            self.current.filter = ""
            self.notify("Выделение и фильтр очищены")

    def flush(self):
        os.write(sys.stdout.fileno(), self.draw().encode("utf-8", errors="replace"))

    def run(self):
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise RuntimeError("Для интерактивного режима нужен терминал (TTY). Используйте --demo для проверки.")
        old = termios.tcgetattr(sys.stdin.fileno())
        try:
            tty.setraw(sys.stdin.fileno())
            os.write(sys.stdout.fileno(), b"\x1b[?1049h\x1b[?25l\x1b[2J")
            self.flush()
            while self.running:
                try:
                    key = self.input.next(0.4)
                    new_size = self.size()
                    if key is not None:
                        self.keypress(key)
                        self.flush()
                    elif new_size != self._last_size:
                        self.flush()
                    self._last_size = new_size
                except (OSError, ValueError) as exc:
                    self.notify("Ошибка: " + str(exc))
                    self.flush()
        finally:
            os.write(sys.stdout.fileno(), b"\x1b[0m\x1b[?25h\x1b[?1049l")
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old)
