"""Independent checks for scoped navigation and line evidence."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.git_store import KnowledgeStore
from brain.navigation import read_lines, search, tree


class MarkdownNavigation(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.store = KnowledgeStore(self.root / "store")
        self.areas = self.store.ensure_layout()
        (self.areas["active"] / "current.md").write_text("# План\nТаймаут 5 с\nСсылка на правило\n", encoding="utf-8")
        (self.areas["active"] / "note.md").write_text("# Заметка\n", encoding="utf-8")
        (self.areas["archive"] / "old.md").write_text("# RFC\nТаймаут 8 с\n", encoding="utf-8")

    def test_tree_and_casefolded_search_only_cover_selected_area(self):
        self.assertEqual(tree(self.store), ["current.md", "note.md"])
        self.assertEqual(search(self.store, "ТАЙМАУТ"), [
            {"path": "current.md", "line": 2, "text": "Таймаут 5 с"},
        ])
        self.assertEqual(search(self.store, "таймаут", area="archive"), [
            {"path": "old.md", "line": 2, "text": "Таймаут 8 с"},
        ])

    def test_inclusive_line_range_and_invalid_requests(self):
        self.assertEqual(read_lines(self.store, "current.md", 2, 3).splitlines(),
                         ["Таймаут 5 с", "Ссылка на правило"])
        for path, start, end in (("current.md", 0, 1), ("current.md", 3, 2), ("../archive/old.md", 1, 2)):
            with self.subTest(path=path, start=start, end=end), self.assertRaises(ValueError):
                read_lines(self.store, path, start, end)
