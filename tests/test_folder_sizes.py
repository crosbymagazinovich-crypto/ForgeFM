"""Headless tests for recursive folder sizing and safety."""
import os
from pathlib import Path
import tempfile
import threading
import unittest

from core import directory_size, human_size


class DirectorySizesTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)

    def test_recursive_nested_content(self):
        nested = self.root / 'nested'
        (nested / 'deep').mkdir(parents=True)
        (self.root / 'note.txt').write_bytes(b'abc')
        (nested / 'hidden').write_bytes(b'12345')
        (nested / 'deep' / 'inner').write_bytes(b'1234567')
        result = directory_size(self.root)
        self.assertEqual(result.bytes, 15)
        self.assertEqual(result.files, 3)
        self.assertEqual(result.directories, 2)
        self.assertEqual(result.errors, 0)

    def test_empty_directory(self):
        self.assertEqual(directory_size(self.root).bytes, 0)

    @unittest.skipUnless(hasattr(os, 'symlink'), 'symlinks unavailable')
    def test_no_symlink_loop_or_external_content(self):
        outer = self.root / 'outer'
        outer.mkdir()
        (outer / 'a').write_bytes(b'a' * 12)
        (outer / 'loop').symlink_to(self.root, target_is_directory=True)
        (self.root / 'link').symlink_to(outer, target_is_directory=True)
        result = directory_size(self.root)
        self.assertEqual(result.bytes, 12)
        self.assertEqual(result.files, 1)
        self.assertEqual(result.directories, 1)
        with self.assertRaises(NotADirectoryError):
            directory_size(self.root / 'link')

    def test_cancellation(self):
        event = threading.Event()
        event.set()
        self.assertIsNone(directory_size(self.root, event))

    def test_regular_file_is_not_a_folder(self):
        file = self.root / 'one'
        file.write_bytes(b'xyz')
        with self.assertRaises(NotADirectoryError):
            directory_size(file)

    def test_size_units(self):
        self.assertEqual(human_size(0), '0 Б')
        self.assertEqual(human_size(1024 * 1024), '1.0 МиБ')


if __name__ == '__main__':
    unittest.main()
