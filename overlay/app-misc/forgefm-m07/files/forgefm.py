#!/usr/bin/env python3
"""ForgeFM: a two-pane file manager for Gentoo Linux, built with Tkinter."""

from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import queue
import shlex
import shutil
import subprocess
import sys
import threading

try:
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk
except ImportError:
    sys.exit("Не найден tkinter. В Gentoo включите USE=\"tk\" для dev-lang/python и пересоберите Python.")

from core import (
    APP_NAME,
    create_folder,
    human_size,
    normalize_path,
    read_config,
    rename_item,
    scan_directory,
    transfer_many,
    trash_many,
    write_config,
)

VERSION = "0.2.0 M/07"

PALETTES = {
    "synchroframe": {
        "base": "#140f17", "panel": "#1c1720", "row": "#29212b",
        "text": "#e6dce8", "muted": "#9c90a4", "accent": "#ff5865",
        "selection": "#403342", "border": "#514356", "button": "#29212b",
    },
    "dark": {
        "base": "#140f17", "panel": "#1c1720", "row": "#29212b",
        "text": "#e6dce8", "muted": "#9c90a4", "accent": "#ff5865",
        "selection": "#403342", "border": "#514356", "button": "#29212b",
    },
    "light": {
        "base": "#ece7ed", "panel": "#fdf9ff", "row": "#f1e9f5",
        "text": "#291c31", "muted": "#76677a", "accent": "#a52a3c",
        "selection": "#f7d1d7", "border": "#b9a6c0", "button": "#e4d9e9",
    },
}


class FilePane(tk.Frame):
    def __init__(self, parent: tk.Widget, app: "ForgeFM", initial: Path, side: str):
        super().__init__(parent, bd=0, highlightthickness=2)
        self.app = app
        self.side = side
        self.directory = Path.home()
        self.entries = []
        self.by_iid = {}
        self.history: list[Path] = []
        self.history_pos = -1
        self.sort_column = "name"
        self.descending = False
        self.path_var = tk.StringVar()
        self.filter_var = tk.StringVar()

        top = ttk.Frame(self, padding=(5, 7))
        top.pack(fill="x")
        for text, method, hint in (
            ("◀", self.back, "Назад"),
            ("▶", self.forward, "Вперёд"),
            ("↑", self.up, "На уровень выше"),
            ("⌂", self.home, "Домой"),
            ("⟳", self.refresh, "Обновить"),
        ):
            button = ttk.Button(top, text=text, width=3, command=method)
            button.pack(side="left", padx=(0, 3))

        self.path_entry = ttk.Entry(top, textvariable=self.path_var)
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(5, 2))
        self.path_entry.bind("<Return>", lambda event: self.navigate_from_entry())
        self.path_entry.bind("<FocusIn>", lambda event: self.app.set_active(self))

        filters = ttk.Frame(self, padding=(8, 0, 8, 6))
        filters.pack(fill="x")
        ttk.Label(filters, text="Фильтр:").pack(side="left", padx=(0, 5))
        self.filter_entry = ttk.Entry(filters, textvariable=self.filter_var, width=25)
        self.filter_entry.pack(side="left", fill="x", expand=True)
        self.filter_entry.bind("<FocusIn>", lambda event: self.app.set_active(self))
        ttk.Button(filters, text="×", width=3, command=lambda: self.filter_var.set("")).pack(side="left", padx=(5, 0))

        table_frame = ttk.Frame(self)
        table_frame.pack(fill="both", expand=True, padx=7)
        self.tree = ttk.Treeview(
            table_frame, columns=("name", "size", "kind", "modified"),
            show="headings", selectmode="extended",
        )
        self.tree.heading("name", text="Имя", command=lambda: self.change_sort("name"))
        self.tree.heading("size", text="Размер", command=lambda: self.change_sort("size"))
        self.tree.heading("kind", text="Тип", command=lambda: self.change_sort("kind"))
        self.tree.heading("modified", text="Изменён", command=lambda: self.change_sort("modified"))
        self.tree.column("name", width=265, minwidth=120, stretch=True)
        self.tree.column("size", width=85, minwidth=75, anchor="e", stretch=False)
        self.tree.column("kind", width=90, minwidth=70, stretch=False)
        self.tree.column("modified", width=125, minwidth=110, stretch=False)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda event: self.open_selected())
        self.tree.bind("<Return>", lambda event: (self.open_selected(), "break")[1])
        self.tree.bind("<BackSpace>", lambda event: (self.up(), "break")[1])
        self.tree.bind("<Tab>", lambda event: (self.app.switch_pane(), "break")[1])
        self.tree.bind("<FocusIn>", lambda event: self.app.set_active(self))
        self.tree.bind("<Button-1>", lambda event: self.app.set_active(self))
        self.tree.bind("<Button-3>", self.show_menu)
        self.tree.bind("<<TreeviewSelect>>", lambda event: self.update_status())

        self.footer_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.footer_var, padding=(9, 6)).pack(fill="x")
        self.filter_var.trace_add("write", lambda *_: self.render())
        self.set_colors(False)
        if not self.navigate(initial):
            self.navigate(Path.home())

    def set_colors(self, active: bool) -> None:
        colors = self.app.colors
        self.configure(
            bg=colors["base"],
            highlightbackground=colors["accent"] if active else colors["border"],
            highlightcolor=colors["accent"] if active else colors["border"],
        )
        self.tree.tag_configure("directory", foreground=colors["accent"])
        self.tree.tag_configure("link", foreground=colors["muted"])

    def navigate(self, destination: Path, history_mode: str = "add") -> bool:
        target = normalize_path(destination)
        try:
            if not target.is_dir():
                raise NotADirectoryError(str(target))
            entries = scan_directory(target)
        except OSError as exc:
            messagebox.showerror("Ошибка открытия", f"Не удалось открыть папку:\n{exc}", parent=self.app.root)
            return False
        self.directory = target
        self.entries = entries
        self.path_var.set(str(target))
        if history_mode == "add":
            if self.history_pos < 0 or self.history[self.history_pos] != target:
                self.history = self.history[:self.history_pos + 1] + [target]
                self.history_pos = len(self.history) - 1
        self.filter_var.set("")
        self.render()
        return True

    def refresh(self) -> None:
        try:
            self.entries = scan_directory(self.directory)
            self.render()
        except OSError:
            # The current directory may have been moved or deleted.
            parent = self.directory.parent
            if not self.navigate(parent):
                self.navigate(Path.home())

    def navigate_from_entry(self) -> None:
        if self.navigate(Path(self.path_var.get())):
            self.tree.focus_set()
        else:
            self.path_var.set(str(self.directory))

    def back(self) -> None:
        if self.history_pos > 0:
            target = self.history[self.history_pos - 1]
            if self.navigate(target, "existing"):
                self.history_pos -= 1

    def forward(self) -> None:
        if self.history_pos + 1 < len(self.history):
            target = self.history[self.history_pos + 1]
            if self.navigate(target, "existing"):
                self.history_pos += 1

    def up(self) -> None:
        if self.directory != self.directory.parent:
            self.navigate(self.directory.parent)

    def home(self) -> None:
        self.navigate(Path.home())

    def change_sort(self, column: str) -> None:
        if self.sort_column == column:
            self.descending = not self.descending
        else:
            self.sort_column = column
            self.descending = False
        self.render()

    def render(self) -> None:
        if not hasattr(self, "tree"):
            return
        old_selection = set(self.selected_paths())
        query = self.filter_var.get().casefold().strip()
        visible = [
            item for item in self.entries
            if (self.app.show_hidden.get() or not item.name.startswith("."))
            and (not query or query in item.name.casefold())
        ]
        def sort_value(item):
            if self.sort_column == "size":
                return (item.size, item.name.casefold())
            if self.sort_column == "modified":
                return (item.modified, item.name.casefold())
            if self.sort_column == "kind":
                return (item.path.suffix.casefold(), item.name.casefold())
            return item.name.casefold()
        # Always show directories first, regardless of sort direction.
        folders = sorted((i for i in visible if i.is_dir), key=sort_value, reverse=self.descending)
        files = sorted((i for i in visible if not i.is_dir), key=sort_value, reverse=self.descending)
        existing = self.tree.get_children()
        if existing:
            self.tree.delete(*existing)
        self.by_iid.clear()
        to_select = []
        for idx, item in enumerate(folders + files):
            if item.is_link:
                prefix, kind, tags = "↗  ", "Ссылка", ("link",)
            elif item.is_dir:
                prefix, kind, tags = "▸  ", "Папка", ("directory",)
            else:
                prefix, kind, tags = "   ", item.path.suffix[1:].upper() or "Файл", ()
            try:
                modified = datetime.fromtimestamp(item.modified).strftime("%d.%m.%y %H:%M")
            except (OSError, OverflowError, ValueError):
                modified = "—"
            iid = str(idx)
            self.by_iid[iid] = item
            self.tree.insert("", "end", iid=iid, values=(
                prefix + item.name,
                "—" if item.is_dir else human_size(item.size),
                kind,
                modified,
            ), tags=tags)
            if item.path in old_selection:
                to_select.append(iid)
        if to_select:
            self.tree.selection_set(to_select)
        self.footer_var.set(f"{len(visible)} элементов · всего {len(self.entries)}")
        self.update_status()

    def selected_paths(self) -> list[Path]:
        return [self.by_iid[key].path for key in self.tree.selection() if key in self.by_iid]

    def open_selected(self) -> None:
        selected = self.selected_paths()
        if not selected:
            return
        target = selected[0]
        if target.is_dir():
            self.navigate(target)
        else:
            self.app.open_external(target)

    def select_path(self, path: Path) -> None:
        for iid, entry in self.by_iid.items():
            if entry.path == path:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                break

    def update_status(self) -> None:
        if self.app.active is not self or self.app.busy:
            return
        selected = self.selected_paths()
        count = len(selected)
        if count:
            self.app.status_var.set(f"{self.directory}  |  Выбрано: {count}")
        else:
            self.app.status_var.set(str(self.directory))

    def show_menu(self, event) -> None:
        self.app.set_active(self)
        row = self.tree.identify_row(event.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
        menu = tk.Menu(self, tearoff=False)
        for label, callback in (
            ("Открыть", self.open_selected),
            ("Копировать в другую панель  F5", self.app.copy_to_other),
            ("Переместить в другую панель  F6", self.app.move_to_other),
            ("Переименовать  F2", self.app.rename_selected),
            ("В корзину  F8", self.app.trash_selected),
        ):
            menu.add_command(label=label, command=callback)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()


class ForgeFM:
    def __init__(self, left: Path | None = None, right: Path | None = None):
        self.config = read_config()
        self.root = tk.Tk()
        self.root.title("▰ M/07  ▰▰▱ SYNCHROFRAME // FORGEFM")
        self.root.geometry("1240x730")
        self.root.minsize(850, 440)
        self.colors = PALETTES[self.config["theme"] if self.config["theme"] in PALETTES else "dark"]
        self.show_hidden = tk.BooleanVar(value=self.config["show_hidden"])
        self.theme_var = tk.StringVar(value=self.config["theme"])
        self.status_var = tk.StringVar(value="Готово")
        self.busy = False
        self.active: FilePane | None = None
        self.clipboard_paths: list[Path] = []
        self.clipboard_move = False
        self.work_queue: queue.Queue = queue.Queue()
        self._style()
        self._menus()
        self._toolbar()

        self.paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, sashwidth=7, bd=0, showhandle=False)
        self.paned.pack(fill="both", expand=True, padx=9, pady=(4, 6))
        left_path = left or Path(self.config["start_left"]).expanduser()
        right_path = right or Path(self.config["start_right"]).expanduser()
        if left is None and not left_path.is_dir():
            left_path = Path.home()
        if right is None and not right_path.is_dir():
            right_path = Path.home()
        self.left = FilePane(self.paned, self, left_path, "left")
        self.right = FilePane(self.paned, self, right_path, "right")
        self.paned.add(self.left, minsize=300)
        self.paned.add(self.right, minsize=300)
        self.set_active(self.left)
        self._bottom_bar()
        self._bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(50, self.left.tree.focus_set)
        self.root.after(250, lambda: self.paned.sash_place(0, self.paned.winfo_width() // 2, 1))

    def _style(self) -> None:
        c = self.colors
        self.root.configure(bg=c["base"])
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=c["base"], foreground=c["text"], font=("DejaVu Sans Mono", 10))
        style.configure("TFrame", background=c["base"])
        style.configure("TLabel", background=c["base"], foreground=c["text"])
        style.configure("TButton", background=c["button"], foreground=c["text"], borderwidth=1, padding=(8, 5), relief="flat")
        style.map("TButton", background=[("active", c["selection"])], foreground=[("disabled", c["muted"])])
        style.configure("TEntry", fieldbackground=c["panel"], foreground=c["text"], insertcolor=c["text"], padding=5)
        style.configure("Treeview", background=c["panel"], fieldbackground=c["panel"], foreground=c["text"], rowheight=29, borderwidth=0, font=("DejaVu Sans Mono", 10))
        style.map("Treeview", background=[("selected", c["selection"])], foreground=[("selected", c["text"])])
        style.configure("Treeview.Heading", background=c["button"], foreground=c["accent"], padding=(7, 8), relief="flat", font=("DejaVu Sans Mono", 10, "bold"))
        style.map("Treeview.Heading", background=[("active", c["selection"])])
        style.configure("TCheckbutton", background=c["base"], foreground=c["text"])
        if hasattr(self, "paned"):
            self.paned.configure(bg=c["border"])
        for pane in (getattr(self, "left", None), getattr(self, "right", None)):
            if pane:
                pane.set_colors(pane is self.active)

    def _menus(self) -> None:
        menu = tk.Menu(self.root, tearoff=False)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Открыть", command=self.open_selected)
        file_menu.add_command(label="Новая папка  F7", command=self.new_folder)
        file_menu.add_command(label="Переименовать  F2", command=self.rename_selected)
        file_menu.add_separator()
        file_menu.add_command(label="Выход", command=self.close)
        menu.add_cascade(label="Файл", menu=file_menu)

        view_menu = tk.Menu(menu, tearoff=False)
        view_menu.add_checkbutton(label="Показывать скрытые  Ctrl+H", variable=self.show_hidden, command=self.toggle_hidden)
        view_menu.add_separator()
        view_menu.add_radiobutton(label="SYNCHROFRAME M/07", variable=self.theme_var, value="synchroframe", command=self.toggle_theme)
        view_menu.add_radiobutton(label="Светлая тема", variable=self.theme_var, value="light", command=self.toggle_theme)
        menu.add_cascade(label="Вид", menu=view_menu)

        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="Горячие клавиши", command=self.show_shortcuts)
        help_menu.add_command(label="О программе", command=lambda: messagebox.showinfo("ForgeFM", f"ForgeFM {VERSION}\nДвухпанельный менеджер файлов для Linux.\nPython + Tkinter", parent=self.root))
        menu.add_cascade(label="Справка", menu=help_menu)
        self.root.configure(menu=menu)

    def _toolbar(self) -> None:
        toolbar = ttk.Frame(self.root, padding=(10, 8))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="▰ M/07  ▰▰▱ SYNCHROFRAME", foreground=self.colors["accent"], font=("DejaVu Sans Mono", 14, "bold")).pack(side="left", padx=(0, 18))
        ttk.Label(toolbar, text="◇ CORE  ↯ DRIVE", foreground=self.colors["muted"], font=("DejaVu Sans Mono", 10)).pack(side="left", padx=(0, 14))
        ttk.Button(toolbar, text="Копировать", command=self.copy_to_other).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Переместить", command=self.move_to_other).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Обновить", command=self.refresh_all).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Терминал", command=self.open_terminal).pack(side="right", padx=3)
        ttk.Checkbutton(toolbar, text="Скрытые", variable=self.show_hidden, command=self.toggle_hidden).pack(side="right", padx=10)

    def _bottom_bar(self) -> None:
        footer = ttk.Frame(self.root, padding=(9, 3))
        footer.pack(fill="x")
        for label, func in (
            ("F2 RENAME", self.rename_selected),
            ("F5 COPY", self.copy_to_other),
            ("F6 MOVE", self.move_to_other),
            ("F7 MKDIR", self.new_folder),
            ("F8 TRASH", self.trash_selected),
        ):
            ttk.Button(footer, text=label, command=func).pack(side="left", padx=3)
        ttk.Label(self.root, textvariable=self.status_var, anchor="w", padding=(13, 7)).pack(fill="x")

    def _bind_keys(self) -> None:
        bindings = {
            "<F2>": self.rename_selected,
            "<F5>": self.copy_to_other,
            "<F6>": self.move_to_other,
            "<F7>": self.new_folder,
            "<F8>": self.trash_selected,
            "<Control-r>": self.refresh_all,
            "<Control-l>": self.focus_address,
            "<Control-f>": self.focus_filter,
            "<Control-h>": self.toggle_hidden_from_key,
            "<Control-Tab>": self.switch_pane,
            "<Alt-Left>": self.go_back,
            "<Alt-Right>": self.go_forward,
        }
        for key, callback in bindings.items():
            self.root.bind_all(key, lambda _event, fn=callback: (fn(), "break")[1])
        for key, callback in (
            ("<Control-c>", self.clipboard_copy),
            ("<Control-x>", self.clipboard_cut),
            ("<Control-v>", self.clipboard_paste),
            ("<Delete>", self.trash_selected),
        ):
            self.root.bind_all(key, lambda event, fn=callback: self._file_shortcut(event, fn))

    def _file_shortcut(self, event, callback):
        # Keep standard clipboard behavior in the address and filter fields.
        if isinstance(event.widget, (tk.Entry, ttk.Entry, tk.Text)):
            return None
        callback()
        return "break"

    def set_active(self, pane: FilePane) -> None:
        self.active = pane
        for other in (getattr(self, "left", None), getattr(self, "right", None)):
            if other:
                other.set_colors(other is pane)
        pane.update_status()

    def opposite(self) -> FilePane:
        return self.right if self.active is self.left else self.left

    def switch_pane(self) -> None:
        other = self.opposite()
        self.set_active(other)
        other.tree.focus_set()

    def toggle_hidden(self) -> None:
        self.config["show_hidden"] = self.show_hidden.get()
        self.save_settings()
        for pane in (self.left, self.right):
            pane.render()

    def toggle_hidden_from_key(self) -> None:
        self.show_hidden.set(not self.show_hidden.get())
        self.toggle_hidden()

    def toggle_theme(self) -> None:
        self.config["theme"] = self.theme_var.get()
        self.colors = PALETTES.get(self.config["theme"], PALETTES["dark"])
        self._style()
        self.save_settings()

    def save_settings(self) -> None:
        try:
            write_config(self.config)
        except OSError as exc:
            messagebox.showwarning("Настройки", f"Не удалось сохранить настройки:\n{exc}", parent=self.root)

    def selected(self) -> list[Path]:
        return self.active.selected_paths() if self.active else []

    def _need_selection(self) -> list[Path]:
        paths = self.selected()
        if not paths:
            self.status_var.set("Выберите файл или папку в активной панели")
        return paths

    def refresh_all(self) -> None:
        self.left.refresh()
        self.right.refresh()

    def open_selected(self) -> None:
        self.active.open_selected()

    def open_external(self, path: Path) -> None:
        opener = shutil.which("xdg-open")
        if not opener:
            messagebox.showerror("Открыть файл", "Не найден xdg-open (Gentoo: x11-misc/xdg-utils)", parent=self.root)
            return
        try:
            subprocess.Popen([opener, str(path)], cwd=path.parent, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            messagebox.showerror("Открыть файл", str(exc), parent=self.root)

    def open_terminal(self) -> None:
        configured = self.config.get("terminal", "auto")
        candidates = ([shlex.split(configured)] if configured != "auto" else [
            ["foot"], ["kitty"], ["alacritty"], ["xfce4-terminal"],
            ["konsole"], ["gnome-terminal"], ["xterm"],
        ])
        for command in candidates:
            if command and shutil.which(command[0]):
                try:
                    subprocess.Popen(command, cwd=self.active.directory, start_new_session=True)
                    return
                except OSError as exc:
                    messagebox.showerror("Терминал", str(exc), parent=self.root)
                    return
        messagebox.showwarning("Терминал", "Терминал не найден. Укажите его в ~/.config/forgefm/config.json", parent=self.root)

    def run_task(self, description: str, worker, after_success=None) -> None:
        if self.busy:
            messagebox.showinfo("Операция выполняется", "Сначала завершите текущую операцию.", parent=self.root)
            return
        self.busy = True
        self.status_var.set(description + "…")

        def invoke():
            try:
                result = worker()
                self.work_queue.put((True, result, description, after_success))
            except Exception as exc:
                self.work_queue.put((False, str(exc), description, None))

        threading.Thread(target=invoke, name="forgefm-file-op", daemon=False).start()
        self.root.after(80, self.poll_task)

    def poll_task(self) -> None:
        try:
            ok, result, description, callback = self.work_queue.get_nowait()
        except queue.Empty:
            self.root.after(80, self.poll_task)
            return
        self.busy = False
        self.refresh_all()
        if ok:
            if callback:
                callback(result)
            self.status_var.set(description + " — завершено")
        else:
            self.status_var.set(description + " — ошибка")
            messagebox.showerror("Ошибка операции", f"{description}:\n{result}", parent=self.root)

    def copy_to_other(self) -> None:
        self._transfer_to_other(False)

    def move_to_other(self) -> None:
        self._transfer_to_other(True)

    def _transfer_to_other(self, move: bool) -> None:
        paths = self._need_selection()
        if not paths:
            return
        target = self.opposite().directory
        name = "Перемещение" if move else "Копирование"
        self.run_task(f"{name} ({len(paths)}) → {target}", lambda: transfer_many(paths, target, move=move))

    def clipboard_copy(self) -> None:
        paths = self._need_selection()
        if paths:
            self.clipboard_paths = paths
            self.clipboard_move = False
            self.status_var.set(f"В буфере: {len(paths)} (копировать). Ctrl+V для вставки")

    def clipboard_cut(self) -> None:
        paths = self._need_selection()
        if paths:
            self.clipboard_paths = paths
            self.clipboard_move = True
            self.status_var.set(f"В буфере: {len(paths)} (переместить). Ctrl+V для вставки")

    def clipboard_paste(self) -> None:
        if not self.clipboard_paths:
            self.status_var.set("Буфер файлов пуст")
            return
        target = self.active.directory
        sources = list(self.clipboard_paths)
        move = self.clipboard_move
        self.run_task("Вставка файлов", lambda: transfer_many(sources, target, move=move),
                      after_success=(lambda result: self.clear_clipboard()) if move else None)

    def clear_clipboard(self) -> None:
        self.clipboard_paths = []
        self.clipboard_move = False

    def rename_selected(self) -> None:
        if self.busy:
            return
        paths = self._need_selection()
        if len(paths) != 1:
            if paths:
                messagebox.showinfo("Переименовать", "Выберите один элемент.", parent=self.root)
            return
        source = paths[0]
        name = simpledialog.askstring("Переименовать", "Новое имя:", initialvalue=source.name, parent=self.root)
        if name is None:
            return
        try:
            target = rename_item(source, name)
            self.refresh_all()
            self.active.select_path(target)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Переименовать", str(exc), parent=self.root)

    def new_folder(self) -> None:
        if self.busy:
            return
        name = simpledialog.askstring("Создать папку", "Название новой папки:", parent=self.root)
        if name is None:
            return
        try:
            path = create_folder(self.active.directory, name)
            self.refresh_all()
            self.active.select_path(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Новая папка", str(exc), parent=self.root)

    def trash_selected(self) -> None:
        paths = self._need_selection()
        if not paths:
            return
        if self.config["confirm_trash"] and not messagebox.askyesno(
            "В корзину", f"Переместить в корзину выбранные элементы ({len(paths)})?",
            icon="warning", parent=self.root,
        ):
            return
        self.run_task(f"Отправка в корзину ({len(paths)})", lambda: trash_many(paths))

    def focus_address(self) -> None:
        self.active.path_entry.focus_set()
        self.active.path_entry.select_range(0, "end")

    def focus_filter(self) -> None:
        self.active.filter_entry.focus_set()
        self.active.filter_entry.select_range(0, "end")

    def go_back(self) -> None:
        self.active.back()

    def go_forward(self) -> None:
        self.active.forward()

    def show_shortcuts(self) -> None:
        messagebox.showinfo("Горячие клавиши", (
            "F2 — переименовать\n"
            "F5 — копировать в другую панель\n"
            "F6 — переместить в другую панель\n"
            "F7 — создать папку\n"
            "F8 / Delete — в корзину\n"
            "Ctrl+C / Ctrl+X / Ctrl+V — файловый буфер\n"
            "Ctrl+L — адресная строка\n"
            "Ctrl+F — фильтр файлов в текущей папке\n"
            "Ctrl+H — скрытые файлы\n"
            "Ctrl+R — обновить\n"
            "Tab — перейти к другой панели\n"
            "Alt+← / Alt+→ — история\n"
            "Backspace — вверх (в таблице)\n"
            "Enter / двойной щелчок — открыть"
        ), parent=self.root)

    def close(self) -> None:
        if self.busy:
            messagebox.showwarning("Идёт операция", "Файловая операция ещё выполняется. Дождитесь её завершения, чтобы избежать неполного копирования.", parent=self.root)
            return
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="ForgeFM — файловый менеджер для Gentoo Linux")
    parser.add_argument("paths", nargs="*", help="Начальные каталоги левой и правой панели")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args()
    if len(args.paths) > 2:
        parser.error("Можно указать не более двух каталогов")
    left = Path(args.paths[0]) if args.paths else None
    right = Path(args.paths[1]) if len(args.paths) > 1 else None
    try:
        app = ForgeFM(left, right)
        app.root.mainloop()
    except tk.TclError as exc:
        sys.exit(f"Не удалось запустить графический интерфейс Tk: {exc}")


if __name__ == "__main__":
    main()
