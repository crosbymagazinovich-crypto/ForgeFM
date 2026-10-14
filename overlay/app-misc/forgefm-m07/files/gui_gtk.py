"""ForgeFM M/07 single-window GTK4 desktop UI. Requires PyGObject + GTK4."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from core import (
    create_folder, directory_size, normalize_path,
    read_config, rename_item, scan_directory, transfer_many, trash_many, write_config,
)
from presentation import date_text, kind_text, readable_name, size_presentation

CSS = """
/* M/07: red Konsole-inspired accent, system Unicode-friendly text. */
window, .main-bg { background: #140f17; color: #e6dce8; }
label { color: #e6dce8; font-family: sans-serif; }
.titleband { background: #1d1720; border-bottom: 1px solid #514356; min-height: 40px; }
.brand { font-family: sans-serif; font-weight: 800; font-size: 18px; color: #ff5865; letter-spacing: 1px; }
.subbrand { color: #b6a6c1; font-family: monospace; font-size: 11px; }
.status-led { color: #ff5865; font-family: monospace; font-size: 12px; font-weight: bold; }
.sidebar { background: #19131d; border-right: 1px solid #514356; }
.sidebar-title, .section { color: #ff7581; font-family: monospace; font-size: 11px; font-weight: bold; }
.places, .places row { background: transparent; }
.places row { border-radius: 7px; margin: 2px 7px; }
.places row:hover { background: #35232f; }
.places row:selected { background: #49272f; }
.places row:selected label { color: #ffd5d8; }
.place-label { color: #e6dce8; font-size: 13px; }
.place-icon { color: #ff7581; }
.sidebar button { background: transparent; border: 0; border-radius: 6px; color: #b8a8c0; }
.sidebar button:hover { background: #35232f; color: #ff5865; }
.toolbar { background: #211926; border-bottom: 1px solid #514356; }
button { background: #2b2231; color: #e6dce8; border: 1px solid #514356; border-radius: 5px; padding: 5px 9px; }
button:hover { background: #403046; color: #ff5865; }
button:disabled { opacity: .45; }
button.action { background: #46212b; border-color: #a54053; color: #ffd4d6; }
button.mini { padding: 4px 8px; min-height: 24px; }
entry { background: #211b26; color: #eee6ef; border: 1px solid #655269; border-radius: 5px; padding: 6px; caret-color: #ff5865; }
entry:focus { border-color: #ff5865; }
.headings { background: #211a25; border-bottom: 1px solid #514356; }
.column-title { color: #b9a9c3; font-family: monospace; font-size: 11px; font-weight: bold; }
.file-list { background: #140f17; }
.file-row { border-bottom: 1px solid #2e2633; background: #140f17; }
.file-row:hover { background: #261e2d; }
.file-row:selected { background: #432730; }
.file-name { font-size: 13px; }
.folder-name { color: #ff8990; font-weight: 600; }
.link-name { color: #cba5ff; }
.file-icon { color: #ff8990; }
.muted { color: #b5a8bc; font-size: 12px; }
.size { font-family: monospace; font-size: 12px; color: #e6dce8; }
.bottom { background: #1d1720; border-top: 1px solid #514356; }
.bottom-label { font-family: monospace; font-size: 11px; color: #d5cbd8; }
.toggle:checked { background: #552530; color: #ffd0d3; border-color: #ff5865; }
"""

# Header and rows share the same fixed-width columns. File names use the rest.
COLUMN_WIDTHS = (132, 116, 146)  # size, type, date


def _preferred_directory(*names: str) -> Path:
    for name in names:
        folder = Path.home() / name
        if folder.is_dir():
            return folder
    return Path.home()


class ForgeWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, initial: Path):
        super().__init__(application=app, title="ForgeFM M/07 · SYNCHROFRAME")
        self.set_default_size(1120, 700)
        self.set_size_request(820, 460)
        self.config = read_config()
        self.directory = Path.home()
        self.entries = []
        self.history: list[Path] = []
        self.history_index = -1
        self.rows = {}
        self.place_rows = {}
        self.sizes = {}  # Path -> DirectorySize, NOT a display string
        self.size_labels = {}
        self._size_jobs = deque()
        self._size_active = 0
        self._generation = 0
        self._cancel = threading.Event()
        self._closed = False
        self._busy = False
        self._size_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="forgefm-size")
        self._ops_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="forgefm-files")
        self.connect("close-request", self._on_close)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class("main-bg")
        self.set_child(root)
        self._build_brand(root)
        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        main.set_vexpand(True)
        root.append(main)
        self._build_sidebar(main)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.set_hexpand(True)
        main.append(body)
        self._build_toolbar(body)
        self._build_listing(body)
        self._build_status(root)
        keyboard = Gtk.EventControllerKey.new()
        keyboard.connect("key-pressed", self._on_key)
        self.add_controller(keyboard)
        self.navigate(initial if initial.is_dir() else Path.home())

    @staticmethod
    def _label(text: str, style: str | None = None, xalign: float = 0.0) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=xalign)
        label.set_single_line_mode(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        if style:
            label.add_css_class(style)
        return label

    def _build_brand(self, parent):
        # Restore the original v0.3 tall, two-line SYNCHROFRAME titlebar.
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        bar.add_css_class("titleband")
        bar.set_size_request(-1, 66)
        brand_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        brand_wrap.set_margin_start(18)
        brand_wrap.set_valign(Gtk.Align.CENTER)
        brand_wrap.append(self._label("▰ M/07  ▰▰▱  FORGEFM", "brand"))
        brand_wrap.append(self._label("S Y N C H R O F R A M E  //  FILE SYSTEM", "subbrand"))
        bar.append(brand_wrap)
        stretch = Gtk.Box()
        stretch.set_hexpand(True)
        bar.append(stretch)
        led = self._label("● ONLINE  //  GENTOO", "status-led")
        led.set_margin_end(18)
        led.set_valign(Gtk.Align.CENTER)
        bar.append(led)
        parent.append(bar)

    @staticmethod
    def _icon(name: str, pixels: int, style: str = "place-icon"):
        icon = Gtk.Image.new_from_icon_name(name)
        icon.set_pixel_size(pixels)
        icon.add_css_class(style)
        return icon

    def _build_sidebar(self, parent):
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sidebar.set_size_request(198, -1)
        sidebar.add_css_class("sidebar")
        header = self._label("◇ CORE  /  PLACES", "sidebar-title")
        header.set_margin_top(13)
        header.set_margin_start(14)
        header.set_margin_bottom(4)
        sidebar.append(header)

        self.places = Gtk.ListBox()
        self.places.add_css_class("places")
        self.places.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.places.set_activate_on_single_click(False)  # both panels: double click to navigate
        self.places.connect("row-activated", self._on_place_activated)
        for caption, icon_name, destination in (
            ("Домашняя", "user-home-symbolic", Path.home()),
            ("Загрузки", "folder-download-symbolic", _preferred_directory("Загрузки", "Downloads")),
            ("Документы", "folder-documents-symbolic", _preferred_directory("Документы", "Documents")),
            ("Изображения", "folder-pictures-symbolic", _preferred_directory("Изображения", "Pictures")),
            ("Корневая /", "drive-harddisk-symbolic", Path("/")),
        ):
            # Do not add a misleading shortcut to Home if an XDG folder is absent.
            if destination == Path.home() and caption not in ("Домашняя",):
                continue
            row = Gtk.ListBoxRow()
            row.add_css_class("place-row")
            contents = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            contents.set_margin_start(11)
            contents.set_margin_end(7)
            contents.set_margin_top(7)
            contents.set_margin_bottom(7)
            contents.append(self._icon(icon_name, 26))  # slightly smaller (v0.4: 30px)
            text = self._label(caption, "place-label")
            text.set_hexpand(True)
            contents.append(text)
            row.set_child(contents)
            row.set_tooltip_text(str(destination) + "\nДвойной щелчок для открытия")
            self.places.append(row)
            self.place_rows[row] = destination
        sidebar.append(self.places)

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.set_margin_top(10)
        separator.set_margin_bottom(8)
        sidebar.append(separator)
        info = self._label("↯ DRIVE  /  ACTIONS", "sidebar-title")
        info.set_margin_start(14)
        sidebar.append(info)
        for name, icon_name, handler in (
            ("Новая папка", "folder-new-symbolic", self.new_folder),
            ("Обновить", "view-refresh-symbolic", self.refresh),
        ):
            button = Gtk.Button()
            button.set_margin_start(7)
            button.set_margin_end(7)
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            content.set_margin_top(5)
            content.set_margin_bottom(5)
            content.append(self._icon(icon_name, 24))
            content.append(self._label(name, "place-label"))
            button.set_child(content)
            button.connect("clicked", lambda _b, callback=handler: callback())
            sidebar.append(button)
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        sidebar.append(spacer)
        footer = self._label("M/07  //  v0.4.1", "subbrand")
        footer.set_margin_start(14)
        footer.set_margin_bottom(14)
        sidebar.append(footer)
        parent.append(sidebar)

    def _on_place_activated(self, _listbox, row):
        destination = self.place_rows.get(row)
        if destination is not None:
            self.navigate(destination)

    def _button(self, parent, label, callback, cls=None):
        b = Gtk.Button(label=label)
        if cls:
            b.add_css_class(cls)
        b.connect("clicked", lambda _button: callback())
        parent.append(b)
        return b

    def _build_toolbar(self, parent):
        area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        area.add_css_class("toolbar")
        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        row1.set_margin_start(10)
        row1.set_margin_end(10)
        row1.set_margin_top(7)
        for icon, hint, callback in (
            ("go-previous-symbolic", "Назад", self.back),
            ("go-next-symbolic", "Вперёд", self.forward),
            ("go-up-symbolic", "На уровень выше", self.up),
            ("user-home-symbolic", "Домашняя папка", lambda: self.navigate(Path.home())),
        ):
            button = Gtk.Button()
            button.add_css_class("mini")
            button.set_child(self._icon(icon, 17))
            button.set_tooltip_text(hint)
            button.connect("clicked", lambda _b, action=callback: action())
            row1.append(button)
        self.path_entry = Gtk.Entry()
        self.path_entry.set_hexpand(True)
        self.path_entry.set_tooltip_text("Путь к папке · Enter для перехода · Ctrl+L")
        self.path_entry.connect("activate", lambda _e: self.navigate(Path(self.path_entry.get_text())))
        row1.append(self.path_entry)
        refresh = Gtk.Button()
        refresh.add_css_class("mini")
        refresh.set_child(self._icon("view-refresh-symbolic", 17))
        refresh.set_tooltip_text("Обновить (F9)")
        refresh.connect("clicked", lambda _b: self.refresh())
        row1.append(refresh)
        area.append(row1)

        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        row2.set_margin_start(10)
        row2.set_margin_end(10)
        row2.set_margin_bottom(7)
        for title, hint, callback in (
            ("F2 Имя", "Переименовать", self.rename),
            ("F5 Копия", "Копировать выбранное", lambda: self.transfer(False)),
            ("F6 Перенос", "Переместить выбранное", lambda: self.transfer(True)),
            ("F7 Папка", "Создать папку", self.new_folder),
            ("F8 Корзина", "Отправить в корзину", self.trash),
        ):
            button = self._button(row2, title, callback, "mini")
            button.set_tooltip_text(hint)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Поиск в папке")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", lambda _w: self._render())
        row2.append(self.search)
        self.hidden_toggle = Gtk.ToggleButton(label="Скрытые")
        self.hidden_toggle.add_css_class("toggle")
        self.hidden_toggle.add_css_class("mini")
        self.hidden_toggle.set_tooltip_text("Показать или скрыть файлы с именами, начинающимися с точки")
        self.hidden_toggle.set_active(self.config.get("show_hidden", False))
        self.hidden_toggle.connect("toggled", self._toggle_hidden)
        row2.append(self.hidden_toggle)
        self.sizes_toggle = Gtk.ToggleButton(label="Размеры ✓")
        self.sizes_toggle.add_css_class("toggle")
        self.sizes_toggle.add_css_class("mini")
        self.sizes_toggle.set_tooltip_text("Считать размер папок включая вложенные файлы")
        self.sizes_toggle.set_active(self.config.get("calculate_folder_sizes", True))
        self.sizes_toggle.connect("toggled", self._toggle_sizes)
        self.sizes_toggle.set_label("Размеры ✓" if self.sizes_toggle.get_active() else "Размеры —")
        row2.append(self.sizes_toggle)
        area.append(row2)
        self._msg = self._label("● READY", "section")
        parent.append(area)

    @staticmethod
    def _column(label: Gtk.Label, index: int) -> None:
        """Apply identical widths to a header or a data row."""
        if index == 0:
            label.set_hexpand(True)
            label.set_size_request(170, -1)
        else:
            label.set_size_request(COLUMN_WIDTHS[index - 1], -1)
            label.set_hexpand(False)
        if index == 1:
            label.set_xalign(1.0)

    def _cells_box(self, first: Gtk.Widget, size: Gtk.Widget,
                   typ: Gtk.Widget, modified: Gtk.Widget):
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        line.set_margin_start(12)
        line.set_margin_end(12)
        for col, widget in enumerate((first, size, typ, modified)):
            self._column(widget, col)
            line.append(widget)
        return line

    def _build_listing(self, parent):
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        heading.add_css_class("headings")
        labels = [self._label(text, "column-title") for text in
                  ("ИМЯ", "РАЗМЕР", "ТИП", "ИЗМЕНЁН")]
        row = self._cells_box(*labels)
        row.set_margin_top(7)
        row.set_margin_bottom(7)
        heading.append(row)
        parent.append(heading)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("file-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        self.listbox.set_activate_on_single_click(False)
        self.listbox.connect("row-activated", self._on_row_activated)
        self.listbox.connect("selected-rows-changed", lambda _w: self._update_status())
        scroll.set_child(self.listbox)
        parent.append(scroll)

    def _build_status(self, parent):
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        foot.add_css_class("bottom")
        # Compact, static status line; never slides out or overlaps the list.
        foot.set_size_request(-1, 30)
        self.status = self._label("READY", "bottom-label")
        self.status.set_margin_start(12)
        self.status.set_hexpand(True)
        foot.append(self.status)
        self._msg.set_margin_end(12)
        foot.append(self._msg)
        parent.append(foot)

    def _on_close(self, *_args):
        self._closed = True
        self._cancel.set()
        self._size_pool.shutdown(wait=False, cancel_futures=True)
        self._ops_pool.shutdown(wait=False, cancel_futures=True)
        return False

    def _update_status(self):
        try:
            selected = len(self.listbox.get_selected_rows())
        except (AttributeError, RuntimeError):
            selected = 0
        total = len(self.entries)
        dirs = sum(x.is_dir for x in self.entries)
        visible = len(self.rows)
        self.status.set_text(
            f"◇ CORE  {readable_name(str(self.directory))}   │   {visible} из {total} элементов · "
            f"{dirs} папок · {selected} выделено"
            + ("  │  ◉ ОПЕРАЦИЯ..." if self._busy else "")
        )

    def navigate(self, target: Path, record=True):
        target = normalize_path(target)
        try:
            if not target.is_dir():
                raise NotADirectoryError(str(target))
            items = scan_directory(target)
        except OSError as exc:
            self._error(f"Не удалось открыть каталог:\n{exc}")
            self.path_entry.set_text(str(self.directory))
            return False
        self.directory = target
        self.entries = items
        self.sizes.clear()
        self.path_entry.set_text(str(target))
        for place_row, place in self.place_rows.items():
            if place == target:
                self.places.select_row(place_row)
                break
        else:
            self.places.unselect_all()
        self.search.set_text("")
        if record:
            if self.history_index < 0 or self.history[self.history_index] != target:
                self.history = self.history[:self.history_index + 1] + [target]
                self.history_index = len(self.history) - 1
        self._render()
        return True

    def refresh(self):
        self.navigate(self.directory, record=False)

    def back(self):
        if self.history_index > 0 and self.navigate(self.history[self.history_index-1], record=False):
            self.history_index -= 1

    def forward(self):
        if self.history_index + 1 < len(self.history) and self.navigate(self.history[self.history_index+1], record=False):
            self.history_index += 1

    def up(self):
        if self.directory != self.directory.parent:
            self.navigate(self.directory.parent)

    def _toggle_hidden(self, button):
        self.config["show_hidden"] = button.get_active()
        write_config(self.config)
        self._render()

    def _toggle_sizes(self, button):
        active = button.get_active()
        self.sizes_toggle.set_label("Размеры ✓" if active else "Размеры —")
        self.config["calculate_folder_sizes"] = active
        write_config(self.config)
        self._render()

    def _render(self):
        # Retire results from an older directory/filter before attaching new labels.
        self._cancel.set()
        self._cancel = threading.Event()
        self._generation += 1
        self._size_jobs.clear()
        self._size_active = 0
        generation = self._generation
        selected = set(self.selected_paths())
        while row := self.listbox.get_row_at_index(0):
            self.listbox.remove(row)
        self.rows.clear()
        self.size_labels.clear()
        query = self.search.get_text().strip().casefold()
        visible = [x for x in self.entries if
                   (self.hidden_toggle.get_active() or not x.name.startswith(".")) and
                   query in x.name.casefold()]
        visible.sort(key=lambda x: (not x.is_dir, x.name.casefold()))
        for item in visible:
            row = Gtk.ListBoxRow()
            row.add_css_class("file-row")
            name_cell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            icon_name = ("emblem-symbolic-link" if item.is_link else
                         "folder-symbolic" if item.is_dir else "text-x-generic-symbolic")
            icon = self._icon(icon_name, 23, "file-icon")
            name_cell.append(icon)
            name = self._label(readable_name(item.name), "file-name")
            name.set_hexpand(True)
            name.set_tooltip_text(str(item.path))
            if item.is_link:
                name.add_css_class("link-name")
            elif item.is_dir:
                name.add_css_class("folder-name")
            name_cell.append(name)
            calculate = self.sizes_toggle.get_active()
            result = self.sizes.get(item.path) if item.is_dir and not item.is_link else None
            display, details = size_presentation(item, calculate, result)
            size = self._label(display, "size", xalign=1.0)
            size.set_tooltip_text(details)
            typ = self._label(kind_text(item), "muted")
            typ.set_tooltip_text(kind_text(item))
            modified = self._label(date_text(item.modified), "muted")
            line = self._cells_box(name_cell, size, typ, modified)
            line.set_margin_top(5)
            line.set_margin_bottom(5)
            row.set_child(line)
            self.listbox.append(row)
            self.rows[row] = item.path
            if item.is_dir and not item.is_link and calculate:
                self.size_labels[item.path] = size
                if result is None:
                    self._size_jobs.append(item.path)
            if item.path in selected:
                self.listbox.select_row(row)
        self._update_status()
        self._pump_sizes(generation)

    def _pump_sizes(self, generation):
        if generation != self._generation or self._closed:
            return
        while self._size_active < 2 and self._size_jobs:
            path = self._size_jobs.popleft()
            self._size_active += 1
            try:
                future = self._size_pool.submit(directory_size, path, self._cancel)
            except RuntimeError:
                return  # shutting down
            future.add_done_callback(
                lambda fut, p=path, g=generation: GLib.idle_add(self._size_completed, p, g, fut)
            )

    def _size_completed(self, path, generation, future):
        if self._closed or generation != self._generation:
            return GLib.SOURCE_REMOVE
        self._size_active -= 1
        label = self.size_labels.get(path)
        try:
            result = future.result()
            if result is not None:
                self.sizes[path] = result
                if label is not None:
                    entry = next((item for item in self.entries if item.path == path), None)
                    if entry is not None:
                        text, details = size_presentation(entry, True, result)
                        label.set_text(text)
                        label.set_tooltip_text(details)
        except (OSError, ValueError) as exc:
            if label is not None:
                label.set_text("Ошибка")
                label.set_tooltip_text(str(exc))
        self._pump_sizes(generation)
        return GLib.SOURCE_REMOVE

    def selected_paths(self):
        return [self.rows[r] for r in self.listbox.get_selected_rows() if r in self.rows]

    def _on_row_activated(self, _listbox, row):
        path = self.rows.get(row)
        if path is None:
            return
        if path.is_dir():
            self.navigate(path)
        else:
            try:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(str(path)).get_uri(), None)
            except GLib.Error as exc:
                self._error(f"Не удалось открыть файл:\n{exc}")

    def _on_key(self, _controller, keyval, _keycode, state):
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_l, Gdk.KEY_L):
            self.path_entry.grab_focus()
            self.path_entry.select_region(0, -1)
            return True
        if isinstance(self.get_focus(), (Gtk.Entry, Gtk.SearchEntry)):
            return False
        if keyval == Gdk.KEY_F2:
            self.rename()
        elif keyval == Gdk.KEY_F5:
            self.transfer(False)
        elif keyval == Gdk.KEY_F6:
            self.transfer(True)
        elif keyval == Gdk.KEY_F7:
            self.new_folder()
        elif keyval in (Gdk.KEY_F8, Gdk.KEY_Delete):
            self.trash()
        elif keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Alt_L):
            if keyval == Gdk.KEY_BackSpace:
                self.up()
            else:
                return False
        elif keyval == Gdk.KEY_F9:
            self.refresh()
        else:
            return False
        return True

    def _error(self, text):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.ERROR,
                                buttons=Gtk.ButtonsType.CLOSE, text=text)
        dlg.connect("response", lambda d, _r: d.destroy())
        dlg.present()

    def _prompt(self, title, initial, callback):
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dlg.add_button("Отмена", Gtk.ResponseType.CANCEL)
        dlg.add_button("Применить", Gtk.ResponseType.ACCEPT)
        entry = Gtk.Entry()
        entry.set_text(initial)
        entry.set_margin_top(18)
        entry.set_margin_bottom(18)
        entry.set_margin_start(18)
        entry.set_margin_end(18)
        entry.set_activates_default(True)
        dlg.set_default_response(Gtk.ResponseType.ACCEPT)
        dlg.get_content_area().append(entry)
        def on_response(dialog, response):
            value = entry.get_text()
            dialog.destroy()
            if response == Gtk.ResponseType.ACCEPT:
                callback(value)
        dlg.connect("response", on_response)
        dlg.present()
        entry.grab_focus()
        entry.select_region(0, -1)

    def _confirm(self, message, callback):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE, text=message)
        dlg.add_button("Отмена", Gtk.ResponseType.CANCEL)
        dlg.add_button("Продолжить", Gtk.ResponseType.ACCEPT)
        def response(d, r):
            d.destroy()
            if r == Gtk.ResponseType.ACCEPT:
                callback()
        dlg.connect("response", response)
        dlg.present()

    def _run_operation(self, title, callback):
        if self._busy:
            self._error("Дождитесь завершения текущей операции")
            return
        self._busy = True
        self._msg.set_text(f"↯ DRIVE  /  {title.upper()}...")
        self._update_status()
        future = self._ops_pool.submit(callback)
        future.add_done_callback(lambda f: GLib.idle_add(self._operation_done, title, f))

    def _operation_done(self, title, future):
        if self._closed:
            return GLib.SOURCE_REMOVE
        self._busy = False
        try:
            future.result()
            self._msg.set_text(f"● DONE  /  {title.upper()}")
        except Exception as exc:
            self._msg.set_text("▲ FAULT  /  FILE OPERATION")
            self._error(f"{title}:\n{exc}")
        self.refresh()
        return GLib.SOURCE_REMOVE

    def new_folder(self):
        if self._busy:
            return
        self._prompt("Создать каталог", "Новая папка", lambda name: self._run_operation("Создание папки", lambda: create_folder(self.directory, name)))

    def rename(self):
        picked = self.selected_paths()
        if self._busy:
            return
        if len(picked) != 1:
            self._error("Для переименования выбери один файл или каталог")
            return
        path = picked[0]
        self._prompt("Переименовать", path.name, lambda name: self._run_operation("Переименование", lambda: rename_item(path, name)))

    def transfer(self, move):
        picked = self.selected_paths()
        if self._busy:
            return
        if not picked:
            self._error("Сначала выдели файл или каталог")
            return
        chooser = Gtk.FileChooserNative.new("Папка назначения", self, Gtk.FileChooserAction.SELECT_FOLDER,
                                            "Выбрать", "Отмена")
        chooser.set_current_folder(Gio.File.new_for_path(str(self.directory)))
        def response(dialog, result):
            try:
                if result == Gtk.ResponseType.ACCEPT:
                    folder = dialog.get_file()
                    if folder is not None and folder.get_path() is not None:
                        dest = Path(folder.get_path())
                        self._run_operation("Перемещение" if move else "Копирование", lambda: transfer_many(picked, dest, move=move))
            finally:
                dialog.destroy()
        chooser.connect("response", response)
        chooser.show()

    def trash(self):
        picked = self.selected_paths()
        if self._busy:
            return
        if not picked:
            self._error("Сначала выдели файл или каталог")
            return
        def action():
            self._run_operation("Отправка в корзину", lambda: trash_many(picked))
        self._confirm(f"Переместить в корзину: {len(picked)} элемент(ов)?", action)


class ForgeApplication(Gtk.Application):
    def __init__(self, start: Path):
        super().__init__(application_id="io.github.forgefm.M07")
        self.start = start
        self.window = None

    def do_activate(self):
        if self.window is None:
            self.window = ForgeWindow(self, self.start)
        self.window.present()


def main(start: Path):
    app = ForgeApplication(start)
    return app.run([])
