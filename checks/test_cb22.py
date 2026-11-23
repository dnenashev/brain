"""Independent checks for the Markdown knowledge store."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.git_store import KnowledgeStore


class MarkdownStore(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.store = KnowledgeStore(self.folder / "knowledge")

    def test_distinct_areas_and_reproducible_archive_import(self):
        paths = self.store.ensure_layout()
        self.assertEqual(set(paths), {"active", "archive", "shared", "inbox", "workspaces"})
        self.assertEqual(len(set(paths.values())), len(paths))
        self.assertTrue(all(path.is_dir() and path.is_relative_to(self.folder / "knowledge") for path in paths.values()))
        source = self.folder / "rfc-7.md"
        content = "# RFC 7\r\nДействовала версия 2.\r\n".encode()
        source.write_bytes(content)
        first = self.store.import_markdown(source, "archive")
        self.assertEqual(first, paths["archive"] / source.name)
        self.assertEqual(first, self.store.import_markdown(source, "archive"))
        self.assertEqual(first.read_bytes(), content)
        self.assertIn("Действовала версия 2.", self.store.read_markdown("archive", "rfc-7.md"))

    def test_conflict_preserves_existing_content(self):
        source = self.folder / "draft.md"
        source.write_text("original", encoding="utf-8")
        saved = self.store.import_markdown(source, "inbox")
        source.write_text("different", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.import_markdown(source, "inbox")
        self.assertEqual(saved.read_text(encoding="utf-8"), "original")

    def test_store_rejects_invalid_areas_and_relative_names(self):
        source = self.folder / "draft.md"
        source.write_text("draft", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.import_markdown(source, "unknown")
        with self.assertRaises(ValueError):
            self.store.read_markdown("active", "../draft.md")
