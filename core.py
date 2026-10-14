"""File operations and configuration for ForgeFM. No GUI dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import stat
from urllib.parse import quote
from uuid import uuid4

APP_NAME = "ForgeFM"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "forgefm"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_CONFIG = {
    "theme": "synchroframe",
    "show_hidden": False,
    "confirm_trash": True,
    "start_left": "~",
    "start_right": "~/Downloads",
    "terminal": "auto",
    "calculate_folder_sizes": True,
}


@dataclass(frozen=True)
class FileEntry:
    name: str
    path: Path
    is_dir: bool
    is_link: bool
    size: int
    modified: float


def read_config(config_file: Path = CONFIG_FILE) -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        with config_file.open(encoding="utf-8") as handle:
            saved = json.load(handle)
        if isinstance(saved, dict):
            for key in DEFAULT_CONFIG:
                if key in saved and isinstance(saved[key], type(DEFAULT_CONFIG[key])):
                    config[key] = saved[key]
    except (OSError, ValueError, UnicodeError):
        pass
    return config


def write_config(config: dict, config_file: Path = CONFIG_FILE) -> None:
    config_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = config_file.with_name(config_file.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, config_file)
    finally:
        temporary.unlink(missing_ok=True)


def normalize_path(path: str | Path) -> Path:
    """Make absolute without resolving symlinks."""
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def scan_directory(directory: Path) -> list[FileEntry]:
    entries: list[FileEntry] = []
    with os.scandir(directory) as iterator:
        for entry in iterator:
            try:
                info = entry.stat(follow_symlinks=False)
                entries.append(FileEntry(
                    name=entry.name,
                    path=Path(entry.path),
                    is_dir=entry.is_dir(follow_symlinks=True),
                    is_link=entry.is_symlink(),
                    size=info.st_size,
                    modified=info.st_mtime,
                ))
            except (OSError, ValueError):
                # A file may disappear during the directory scan.
                continue
    return entries


def human_size(size: int) -> str:
    amount = float(size)
    for unit in ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ", "ПиБ"):
        if amount < 1024 or unit == "ПиБ":
            return f"{amount:.0f} {unit}" if unit == "Б" else f"{amount:.1f} {unit}"
        amount /= 1024
    return "?"


def path_exists(path: Path) -> bool:
    """Unlike Path.exists, handles broken symlinks."""
    return os.path.lexists(path)


def unique_destination(directory: Path, name: str, reserved: set[Path] | None = None) -> Path:
    """Never overwrite existing files, directories or broken symlinks."""
    reserved = reserved or set()
    candidate = directory / name
    if candidate not in reserved and not path_exists(candidate):
        return candidate
    p = Path(name)
    stem, suffix = p.stem, p.suffix
    index = 1
    while True:
        ending = " (копия)" if index == 1 else f" (копия {index})"
        candidate = directory / f"{stem}{ending}{suffix}"
        if candidate not in reserved and not path_exists(candidate):
            return candidate
        index += 1


def _inside_directory(parent: Path, candidate: Path) -> bool:
    """Compare resolved locations to prevent recursive copy/move."""
    root = parent.resolve(strict=False)
    child = candidate.resolve(strict=False)
    return child == root or root in child.parents


def transfer_many(sources: list[Path], destination: Path, *, move: bool = False) -> list[Path]:
    """Copy or move into a directory, preserving symlinks and avoiding overwrites.

    Performs structural validation before the first operation. If an OS error
    occurs mid-batch, previously completed items are not rolled back.
    """
    target = normalize_path(destination)
    if not target.is_dir():
        raise NotADirectoryError(str(target))
    if not sources:
        return []

    validated: list[tuple[Path, Path | None]] = []
    for source in sources:
        src = normalize_path(source)
        if not path_exists(src):
            raise FileNotFoundError(str(src))
        if src.is_dir() and not src.is_symlink() and _inside_directory(src, target):
            raise ValueError(f"Нельзя копировать или перемещать папку внутрь самой себя: {src}")
        if move and src.parent.resolve(strict=False) == target.resolve(strict=False):
            validated.append((src, None))  # already located here
        else:
            validated.append((src, unique_destination(target, src.name)))

    results: list[Path] = []
    reserved: set[Path] = set()
    for src, proposed in validated:
        if proposed is None:
            continue
        # Avoid conflicts among sources with equal names in the same batch.
        dst = unique_destination(target, src.name, reserved)
        reserved.add(dst)
        if move:
            shutil.move(str(src), str(dst))
        elif src.is_dir() and not src.is_symlink():
            shutil.copytree(src, dst, symlinks=True, copy_function=shutil.copy2)
        else:
            shutil.copy2(src, dst, follow_symlinks=False)
        results.append(dst)
    return results


def rename_item(source: Path, new_name: str) -> Path:
    if not new_name or new_name in {".", ".."} or "/" in new_name or "\x00" in new_name:
        raise ValueError("Недопустимое имя файла")
    src = normalize_path(source)
    target = src.with_name(new_name)
    if src == target:
        return src
    if path_exists(target):
        raise FileExistsError(f"Уже существует: {target}")
    src.rename(target)
    return target


def create_folder(directory: Path, name: str) -> Path:
    if not name or name in {".", ".."} or "/" in name or "\x00" in name:
        raise ValueError("Недопустимое имя папки")
    result = normalize_path(directory) / name
    result.mkdir()  # Never replace or silently reuse an existing directory.
    return result


def _trash_dirs() -> tuple[Path, Path]:
    base = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "Trash"
    files = base / "files"
    info = base / "info"
    files.mkdir(parents=True, mode=0o700, exist_ok=True)
    info.mkdir(parents=True, mode=0o700, exist_ok=True)
    return files, info


def trash_one(source: Path) -> Path:
    """Move an item to the home Freedesktop trash, with .trashinfo metadata.

    For items on another filesystem, shutil.move copies then removes the source.
    """
    src = normalize_path(source)
    if not path_exists(src):
        raise FileNotFoundError(str(src))
    if src == Path("/") or src == Path.home():
        raise ValueError("Нельзя отправить в корзину корневую или домашнюю папку")
    files, info = _trash_dirs()
    if _inside_directory(files.parent, src):
        raise ValueError("Файлы, уже находящиеся в корзине, нельзя отправить повторно")
    if src.is_dir() and not src.is_symlink() and _inside_directory(src, files):
        raise ValueError("Нельзя отправить в корзину папку, содержащую саму корзину")
    # Unpredictable name prevents collisions between simultaneous operations.
    target_name = src.name + "." + uuid4().hex[:12]
    target = files / target_name
    info_file = info / (target_name + ".trashinfo")
    metadata = (
        "[Trash Info]\n"
        f"Path={quote(str(src), safe='/')}\n"
        f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n"
    )
    with info_file.open("x", encoding="utf-8") as handle:
        handle.write(metadata)
    try:
        shutil.move(str(src), str(target))
    except Exception:
        info_file.unlink(missing_ok=True)
        # Remove only the destination created by this operation if incomplete.
        try:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif path_exists(target):
                target.unlink()
        except OSError:
            pass
        raise
    return target


def trash_many(sources: list[Path]) -> list[Path]:
    return [trash_one(source) for source in sources]


@dataclass(frozen=True)
class DirectorySize:
    """Logical content size; symlinks are not followed."""
    bytes: int
    files: int
    directories: int
    errors: int


def directory_size(path: Path, cancelled=None) -> DirectorySize | None:
    """Count regular file bytes recursively without following directory symlinks.

    Designed for a background worker; returns None if *cancelled* is set.
    Hidden entries are included, and unreadable subfolders count as errors.
    Hard-linked names are counted separately (logical size, not disk usage).
    """
    root = normalize_path(path)
    if root.is_symlink() or not root.is_dir():
        raise NotADirectoryError(str(root))
    total = files = folders = errors = 0
    pending = [root]
    while pending:
        if cancelled is not None and cancelled.is_set():
            return None
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if cancelled is not None and cancelled.is_set():
                        return None
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        errors += 1
                        continue
                    if stat.S_ISDIR(st.st_mode):
                        folders += 1
                        pending.append(Path(entry.path))
                    elif stat.S_ISREG(st.st_mode):
                        files += 1
                        total += st.st_size
                    # A symlink is intentionally not traversed or counted.
        except OSError:
            errors += 1
    return DirectorySize(total, files, folders, errors)
