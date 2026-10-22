"""Exercise the per-item queue action, including removal of a nonselected clip."""

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    from flugbild.window import Workbench
except ImportError:  # The legacy Qt prototype is optional; the .NET app replaces it.
    raise unittest.SkipTest("PySide6 is not installed")


class QueueTest(unittest.TestCase):
    def test_each_row_removes_its_own_clip(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as scratch:
            files = [Path(scratch) / name for name in ("first.ts", "second.ts")]
            for path in files:
                path.touch()
            window = Workbench()
            try:
                window._add_paths(files)
                self.assertEqual(window.queue_count.text(), "2 clips")
                window.listing.setCurrentRow(0)
                window.queue_rows[1].remove_button.click()
                app.processEvents()
                self.assertEqual(window.clips, [files[0]])
                self.assertEqual(window.listing.count(), 1)
                self.assertEqual(window.queue_count.text(), "1 clip")
                window.queue_rows[0].remove_button.click()
                app.processEvents()
                self.assertEqual(window.clips, [])
                self.assertEqual(window.queue_count.text(), "0 clips")
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
