"""Presentation regressions checked without a running GTK server."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GTK_SOURCE = (ROOT / "gui_gtk.py").read_text(encoding="utf-8")

class DesignTests(unittest.TestCase):
    def test_brand_returned_to_original_header(self):
        self.assertIn('bar.set_size_request(-1, 66)', GTK_SOURCE)
        self.assertIn('S Y N C H R O F R A M E  //  FILE SYSTEM', GTK_SOURCE)
        self.assertIn('.brand { font-family: sans-serif; font-weight: 800; font-size: 18px;', GTK_SOURCE)

    def test_sidebar_icon_sizes(self):
        self.assertIn('contents.append(self._icon(icon_name, 26))', GTK_SOURCE)
        self.assertIn('content.append(self._icon(icon_name, 24))', GTK_SOURCE)

    def test_double_click_navigation_remains(self):
        self.assertEqual(GTK_SOURCE.count('set_activate_on_single_click(False)'), 2)

    def test_red_theme_no_green_colors(self):
        css = GTK_SOURCE.split('CSS = """', 1)[1].split('"""', 1)[0]
        self.assertIn('#ff5865', css)
        for green in ('#52e8b4', '#6ee2cf', '#71eec3', '#72d7bd'):
            self.assertNotIn(green, css)
        self.assertIn('#ff5865', (ROOT / 'assets/forgefm.svg').read_text())

    def test_status_is_not_hover_menu(self):
        self.assertIn('foot.set_size_request(-1, 30)', GTK_SOURCE)
        self.assertNotIn('ДВОЙНОЙ КЛИК / ENTER   ·', GTK_SOURCE)
        self.assertIn('scroll.set_child(self.listbox)', GTK_SOURCE)

if __name__ == '__main__':
    unittest.main()
