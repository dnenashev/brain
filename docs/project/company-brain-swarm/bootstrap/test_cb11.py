"""Public structure checks for the first Company Brain slice."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DevelopmentProcessDocs(unittest.TestCase):
    def check_sections(self, relative, expected):
        document = (ROOT / relative).read_text(encoding="utf-8")
        headings = list(re.finditer(r"^#{1,6} ([^\n]+)$", document, re.MULTILINE))
        sections = {
            heading[1].strip(): document[
                heading.end():headings[index + 1].start() if index + 1 < len(headings) else len(document)
            ].strip()
            for index, heading in enumerate(headings)
        }
        for name in expected:
            with self.subTest(file=relative, section=name):
                self.assertTrue(sections.get(name), f"Need a nonempty section named {name}")

    def test_core_describes_constraints(self):
        self.check_sections("docs/core.md", ("Цель", "Словарь", "Инварианты", "Роли"))

    def test_process_explains_handoff_and_decision(self):
        self.check_sections(
            "docs/processes/development.md",
            ("Вход", "Передача", "Проверка", "Приёмка", "Возврат"),
        )
