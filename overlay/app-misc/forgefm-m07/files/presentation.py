"""Pure, GTK-independent formatting for a readable file listing."""
from __future__ import annotations

from datetime import datetime

from core import DirectorySize, FileEntry, human_size


def readable_name(value: str) -> str:
    """Keep Unicode intact while replacing invalid UTF-8 and control characters."""
    text = value.encode("utf-8", errors="replace").decode("utf-8")
    return text.replace("\n", " ↵ ").replace("\r", " ↵ ").replace("\t", " ⇥ ").replace("\x7f", "�")


def date_text(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y %H:%M")
    except (OSError, OverflowError, ValueError):
        return "—"


def kind_text(entry: FileEntry) -> str:
    if entry.is_link:
        return "Ссылка на папку" if entry.is_dir else "Ссылка"
    if entry.is_dir:
        return "Папка"
    extension = entry.path.suffix.lstrip(".")
    return f"{extension.upper()} · файл" if extension and len(extension) <= 6 else "Файл"


def exact_size(bytes_count: int) -> str:
    return f"{bytes_count:,}".replace(",", " ") + " байт"


def size_presentation(entry: FileEntry, calculate: bool,
                      result: DirectorySize | None = None) -> tuple[str, str]:
    """Return cell text and tooltip, never use a directory's st_size as content size."""
    if entry.is_link:
        return "—", "Символическая ссылка: размер файла или папки назначения не вычисляется"
    if entry.is_dir:
        if not calculate:
            return "—", "Подсчёт размеров папок выключен"
        if result is None:
            return "Подсчёт…", "Вычисление размера папки и её вложенных файлов"
        tooltip = (f"Содержимое: {exact_size(result.bytes)}\n"
                   f"Файлов: {result.files} · вложенных папок: {result.directories}\n"
                   "Включая скрытые файлы, без обхода символических ссылок")
        if result.errors:
            tooltip += f"\nНе удалось прочитать: {result.errors} элементов"
        return human_size(result.bytes) + (" ⚠" if result.errors else ""), tooltip
    return human_size(entry.size), "Размер файла: " + exact_size(entry.size)
