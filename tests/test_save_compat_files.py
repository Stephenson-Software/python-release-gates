"""Unit tests for how the save-compatibility gate counts saves (baseline-write, fixture)."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gates"))
import save_compat  # noqa: E402


class FileCount(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)

    def test_an_empty_slot_directory_is_not_a_save(self):
        (self.root / "slot_1").mkdir()
        d = save_compat.digests(self.root)
        self.assertEqual(d, {"slot_1": "dir"})
        self.assertEqual(save_compat.file_count(d), 0)

    def test_nothing_written_counts_zero(self):
        self.assertEqual(save_compat.file_count(save_compat.digests(self.root)), 0)

    def test_files_are_counted_and_directories_are_not(self):
        (self.root / "slot_1").mkdir()
        (self.root / "slot_1" / "player.json").write_text("{}")
        (self.root / "slot_2").mkdir()
        (self.root / "save.json").write_text("{}")
        self.assertEqual(save_compat.file_count(save_compat.digests(self.root)), 2)

    def test_an_empty_file_is_still_a_file(self):
        (self.root / "save.json").write_bytes(b"")
        self.assertEqual(save_compat.file_count(save_compat.digests(self.root)), 1)


if __name__ == "__main__":
    unittest.main()
