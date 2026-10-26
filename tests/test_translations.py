"""Every language of the app translates every text, with the same placeholders."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import check_translations  # noqa: E402
import collapse_old_releases  # noqa: E402


class TranslationTest(unittest.TestCase):
    def test_every_language_is_complete(self) -> None:
        english = check_translations.interface_texts()
        engine = check_translations.engine_messages()
        self.assertGreater(len(english), 300)
        self.assertIn("Could not read video duration", engine)
        problems = [problem for language in check_translations.LANGUAGES
                    for problem in check_translations.check(language, english, engine)]
        self.assertEqual(problems, [], "\n".join(problems[:40]))


class ReleaseNotesTest(unittest.TestCase):
    NOTES = ("> [!IMPORTANT]\n> launcher\n\n## Download v0.1.0\n\n| table |\n\n## New in 0.1.0\n\n- first\n\n"
             "## New in 0.0.9\n\n- older\n\n## First start\n\n- Windows: run it")

    def test_older_notes_fold_and_keep_every_line(self) -> None:
        folded = collapse_old_releases.fold(self.NOTES, "v0.1.0")
        self.assertIn("<summary><b>New in 0.1.0</b></summary>", folded)
        self.assertIn("<summary><b>New in 0.0.9</b></summary>", folded)
        self.assertIn("<summary>Download v0.1.0 and first start</summary>", folded)
        for line in ("- first", "- older", "| table |", "- Windows: run it", "> launcher"):
            self.assertIn(line, folded)
        self.assertEqual(collapse_old_releases.fold(folded, "v0.1.0"), folded)


if __name__ == "__main__":
    unittest.main()
