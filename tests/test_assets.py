"""The bundled files the engine needs at run time must be found in every layout."""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from flugbild.processor import asset_candidates, caption_workspace, find_asset
from flugbild.recipe import Arbeitsrezept


class AssetTests(unittest.TestCase):
    def test_caption_font_is_shipped_with_the_engine(self):
        font = find_asset("Inter.ttf")
        self.assertTrue(font.is_file())
        self.assertEqual(font.parent.name, "assets")

    def test_frozen_engine_layouts_are_searched(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder) / "_internal"
            with mock.patch("flugbild.processor.sys") as frozen:
                frozen._MEIPASS = str(bundle)
                frozen.executable = str(Path(folder) / "flugbild-engine")
                candidates = asset_candidates("Inter.ttf")
            self.assertIn(bundle / "flugbild" / "assets" / "Inter.ttf", candidates)

    def test_missing_asset_gives_a_clear_message(self):
        with self.assertRaises(FileNotFoundError) as caught:
            find_asset("does-not-exist.bin")
        self.assertIn("does-not-exist.bin", str(caught.exception))
        self.assertIn("download the package again", str(caught.exception))

    def test_no_caption_needs_no_workspace(self):
        # Exports without a caption must not depend on any folder of the installation
        # (the WinError 267 / Errno 2 bug of 0.4.0).
        self.assertIsNone(caption_workspace(Arbeitsrezept(signalart="analog")))

    def test_caption_workspace_holds_font_and_text(self):
        workspace = caption_workspace(Arbeitsrezept(signalart="analog", untertiteltext="Pilot"))
        self.assertIsNotNone(workspace)
        try:
            folder = Path(workspace.name)
            self.assertTrue((folder / "Inter.ttf").is_file())
            self.assertEqual((folder / "caption.txt").read_text(encoding="utf-8"), "Pilot")
        finally:
            workspace.cleanup()


if __name__ == "__main__":
    unittest.main()
