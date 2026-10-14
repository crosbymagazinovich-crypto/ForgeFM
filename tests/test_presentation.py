"""Regression tests for Unicode names, folder contents and consistent sizes."""
import os
from pathlib import Path
import tempfile
import unittest

from core import DirectorySize, FileEntry, directory_size, scan_directory
from presentation import date_text, kind_text, readable_name, size_presentation


class DisplayTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)

    def get_entry(self, name):
        return next(x for x in scan_directory(self.path) if x.name == name)

    def test_russian_name_and_emoji_are_preserved(self):
        name = 'Привет, мир 🎉 (отчёт).txt'
        (self.path / name).write_text('x', encoding='utf-8')
        item = self.get_entry(name)
        self.assertEqual(readable_name(item.name), name)
        self.assertEqual(kind_text(item), 'TXT · файл')

    def test_control_characters_and_invalid_utf8_are_printable(self):
        self.assertEqual(readable_name('имя\nснова\tконец'), 'имя ↵ снова ⇥ конец')
        self.assertEqual(readable_name('broken\udcff'), 'broken?')

    def test_file_size_has_exact_bytes_tooltip(self):
        path = self.path / 'hello.txt'
        path.write_bytes(b'a' * 1536)
        text, tooltip = size_presentation(self.get_entry('hello.txt'), True)
        self.assertEqual(text, '1.5 КиБ')
        self.assertIn('1 536 байт', tooltip)

    def test_folder_size_never_uses_directory_inode_size(self):
        folder = self.path / 'каталог'
        (folder / 'внутри').mkdir(parents=True)
        (folder / 'a').write_bytes(b'abcdef')
        (folder / '.скрытый').write_bytes(b'12')
        (folder / 'внутри' / 'b').write_bytes(b'xyz')
        item = self.get_entry('каталог')
        self.assertTrue(item.is_dir)
        self.assertEqual(size_presentation(item, False)[0], '—')
        self.assertEqual(size_presentation(item, True)[0], 'Подсчёт…')
        info = directory_size(folder)
        self.assertEqual(info.bytes, 11)
        text, tooltip = size_presentation(item, True, info)
        self.assertEqual(text, '11 Б')
        self.assertIn('Файлов: 3', tooltip)
        self.assertIn('вложенных папок: 1', tooltip)

    def test_empty_folder_displays_zero_not_unknown(self):
        folder = self.path / 'empty'
        folder.mkdir()
        item = self.get_entry('empty')
        self.assertEqual(size_presentation(item, True, directory_size(folder))[0], '0 Б')

    def test_unreadable_content_is_marked_in_size(self):
        folder = self.path / 'folder'
        folder.mkdir()
        item = self.get_entry('folder')
        text, tooltip = size_presentation(item, True, DirectorySize(2048, 1, 2, 3))
        self.assertEqual(text, '2.0 КиБ ⚠')
        self.assertIn('Не удалось прочитать: 3', tooltip)

    @unittest.skipUnless(hasattr(os, 'symlink'), 'symlinks unavailable')
    def test_directory_symlink_has_no_recursive_scan(self):
        (self.path / 'target').mkdir()
        (self.path / 'link').symlink_to('target', target_is_directory=True)
        item = self.get_entry('link')
        self.assertTrue(item.is_link)
        self.assertEqual(size_presentation(item, True)[0], '—')
        self.assertEqual(kind_text(item), 'Ссылка на папку')

    def test_invalid_date(self):
        self.assertEqual(date_text(float('inf')), '—')


if __name__ == '__main__':
    unittest.main()
