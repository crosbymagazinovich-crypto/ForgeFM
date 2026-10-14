"""Run: python3 -m unittest discover -s tests -v"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

from core import (
    create_folder,
    human_size,
    path_exists,
    read_config,
    rename_item,
    scan_directory,
    transfer_many,
    trash_one,
    unique_destination,
    write_config,
)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.source = self.root / "source"
        self.destination = self.root / "destination"
        self.source.mkdir()
        self.destination.mkdir()

    def test_copy_file_and_directory(self):
        (self.source / "abc.txt").write_text("content")
        inner = self.source / "folder"
        inner.mkdir()
        (inner / "nested.txt").write_text("nested")
        copied = transfer_many([self.source / "abc.txt", inner], self.destination)
        self.assertEqual(len(copied), 2)
        self.assertEqual((self.destination / "abc.txt").read_text(), "content")
        self.assertEqual((self.destination / "folder" / "nested.txt").read_text(), "nested")
        self.assertTrue((self.source / "folder").exists())

    def test_duplicate_is_not_overwritten(self):
        original = self.destination / "name.txt"
        original.write_text("original")
        (self.source / "name.txt").write_text("changed")
        transfer_many([self.source / "name.txt"], self.destination)
        self.assertEqual(original.read_text(), "original")
        self.assertEqual((self.destination / "name (копия).txt").read_text(), "changed")

    def test_copy_in_same_directory_creates_copy(self):
        path = self.source / "same.txt"
        path.write_text("unchanged")
        result = transfer_many([path], self.source)
        self.assertEqual(result, [self.source / "same (копия).txt"])
        self.assertTrue(path.exists())

    def test_move_in_same_directory_is_noop(self):
        path = self.source / "same.txt"
        path.write_text("same")
        self.assertEqual(transfer_many([path], self.source, move=True), [])
        self.assertTrue(path.exists())

    def test_move_between_directories(self):
        path = self.source / "move.txt"
        path.write_text("move")
        transfer_many([path], self.destination, move=True)
        self.assertFalse(path.exists())
        self.assertEqual((self.destination / "move.txt").read_text(), "move")

    def test_symlink_is_preserved(self):
        (self.source / "file.txt").write_text("data")
        link = self.source / "link"
        link.symlink_to("file.txt")
        transfer_many([link], self.destination)
        dest = self.destination / "link"
        self.assertTrue(dest.is_symlink())
        self.assertEqual(os.readlink(dest), "file.txt")

    def test_broken_symlink_is_preserved(self):
        link = self.source / "broken"
        link.symlink_to("absent")
        self.assertTrue(path_exists(link))
        transfer_many([link], self.destination)
        self.assertEqual(os.readlink(self.destination / "broken"), "absent")

    def test_no_recursive_copy_or_move(self):
        inner = create_folder(self.source, "inner")
        for move in (False, True):
            with self.assertRaises(ValueError):
                transfer_many([self.source], inner, move=move)
        self.assertTrue(inner.exists())

    def test_multipath_collision_no_overwrite(self):
        other = self.root / "other"
        other.mkdir()
        (self.source / "same.txt").write_text("a")
        (other / "same.txt").write_text("b")
        copied = transfer_many([self.source / "same.txt", other / "same.txt"], self.destination)
        self.assertEqual(len(copied), 2)
        self.assertNotEqual(copied[0], copied[1])
        self.assertEqual({p.read_text() for p in copied}, {"a", "b"})

    def test_rename_refuses_overwrite(self):
        src = self.source / "one.txt"
        src.write_text("a")
        (self.source / "two.txt").write_text("b")
        with self.assertRaises(FileExistsError):
            rename_item(src, "two.txt")
        self.assertEqual(src.read_text(), "a")
        with self.assertRaises(ValueError):
            rename_item(src, "../outside.txt")

    def test_create_folder_refuses_existing(self):
        create_folder(self.source, "new")
        with self.assertRaises(FileExistsError):
            create_folder(self.source, "new")

    def test_scan_file_types_and_size(self):
        (self.source / ".hidden").write_text("hi")
        (self.source / "directory").mkdir()
        records = {x.name: x for x in scan_directory(self.source)}
        self.assertIn(".hidden", records)
        self.assertTrue(records["directory"].is_dir)
        self.assertFalse(records[".hidden"].is_dir)
        self.assertEqual(human_size(1024), "1.0 КиБ")

    def test_trash_writes_metadata_and_moves_file(self):
        file = self.source / "привет мир.txt"
        file.write_text("test")
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "data")}):
            target = trash_one(file)
        info = self.root / "data" / "Trash" / "info" / (target.name + ".trashinfo")
        self.assertTrue(target.exists())
        self.assertFalse(file.exists())
        self.assertIn("Path=" + quote(str(file), safe="/"), info.read_text())
        self.assertIn("DeletionDate=", info.read_text())

    def test_cannot_trash_parent_of_trash(self):
        (self.root / "data").mkdir()
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "data")}):
            # Trash lives inside the data directory, so moving data there
            # would cause recursive movement/copying.
            with self.assertRaises(ValueError):
                trash_one(self.root / "data")

    def test_config_round_trip(self):
        file = self.root / "config" / "config.json"
        config = read_config(file)
        self.assertTrue(config["confirm_trash"])
        config["theme"] = "light"
        write_config(config, file)
        self.assertEqual(read_config(file)["theme"], "light")


if __name__ == "__main__":
    unittest.main()
