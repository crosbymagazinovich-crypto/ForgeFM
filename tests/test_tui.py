"""TUI logic tests do not require an interactive terminal."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tui import COLORS, ForgeTUI, Input, Pane, Surface, fitted, git_branch, text_width


class TUITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.left = self.root / 'left'
        self.right = self.root / 'right'
        self.left.mkdir()
        self.right.mkdir()
        (self.left / 'aa.txt').write_text('source')
        (self.left / 'directory').mkdir()
        (self.left / '.hidden').write_text('hidden')
        with patch('tui.read_config', return_value={
            'start_left': str(self.left), 'start_right': str(self.right),
            'show_hidden': False, 'confirm_trash': True, 'theme': 'synchroframe',
        }):
            self.app = ForgeTUI(self.left, self.right)

    def test_hidden_filter_navigation(self):
        p = self.app.current
        self.assertNotIn('.hidden', [x.name for x in p.visible(False)])
        self.assertIn('.hidden', [x.name for x in p.visible(True)])
        p.filter = 'aa'
        self.assertEqual([x.name for x in p.visible(False)], ['aa.txt'])
        p.navigate(self.left / 'directory')
        self.assertEqual(p.filter, '')
        self.assertEqual(p.index, 0)

    def test_marked_copy_and_move_preserve_names(self):
        p = self.app.current
        p.marks.add(self.left / 'aa.txt')
        self.app.transfer(False)
        self.assertEqual((self.right / 'aa.txt').read_text(), 'source')
        self.assertEqual((self.left / 'aa.txt').read_text(), 'source')
        p.marks.add(self.left / 'aa.txt')
        self.app.transfer(False)
        self.assertTrue((self.right / 'aa (копия).txt').exists())
        self.app.confirm = lambda msg: True
        p.marks.add(self.left / 'aa.txt')
        self.app.transfer(True)
        self.assertFalse((self.left / 'aa.txt').exists())
        self.assertTrue((self.right / 'aa (копия 2).txt').exists())

    def test_trash_requires_confirmation(self):
        self.app.current.marks.add(self.left / 'aa.txt')
        self.app.confirm = lambda msg: False
        self.app.trash()
        self.assertTrue((self.left / 'aa.txt').exists())

    def test_safe_key_map(self):
        reader = Input()
        for seq, expected in [(b'\x1b[15~','f5'), (b'\x1b[19~','f8'),
                              (b'\t','tab'), (b'\r','enter'), (b'\x1b[A','up')]:
            reader.buffer = seq
            self.assertEqual(reader.next(), expected)

    def test_render_without_terminal_or_tk(self):
        with patch.object(self.app, 'size', return_value=(96, 28)):
            screen = self.app.draw()
        self.assertIn('SYNCHROFRAME', screen)
        self.assertIn('DRIVE', screen)
        self.assertIn('aa.txt', screen)
        self.assertIn('F5 COPY', screen)
        self.assertTrue(screen.startswith('\x1b[H'))

    def test_font_widths_and_overflow(self):
        self.assertEqual(text_width('Файл'), 4)
        self.assertEqual(text_width('你好'), 4)
        self.assertLessEqual(text_width(fitted('длинное имя файла', 9)), 9)
        board = Surface(20, 4)
        board.put(0, 0, '你好', fg='mint')
        output = board.render()
        self.assertIn('你好', output)
        self.assertIn(COLORS['mint'][1:3].lower(), COLORS['mint'].lower())

    def test_git_branch_reads_only_local_head(self):
        gitdir = self.root / 'project' / '.git'
        gitdir.mkdir(parents=True)
        (gitdir / 'HEAD').write_text('ref: refs/heads/main\n')
        self.assertEqual(git_branch(self.root / 'project'), 'main')


if __name__ == '__main__':
    unittest.main()
