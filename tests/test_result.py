"""Unit tests for the shared result document: `passed` only once the gate finishes (#8)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gates"))
from common import Result, Stop  # noqa: E402


def new_result():
    path = Path(tempfile.mkdtemp()) / "result.json"
    return Result("tests", "owner/repo", "abc", str(path)), path


def on_disk(path):
    return json.loads(path.read_text())


class UnfinishedRun(unittest.TestCase):
    def test_passing_assertions_do_not_read_as_a_pass_before_finish(self):
        result, path = new_result()
        result.check("checkout", True, "HEAD is abc")
        result.check("install-3.12", True, "installed")
        doc = on_disk(path)
        self.assertFalse(doc["passed"])
        self.assertFalse(doc["complete"])
        self.assertEqual([a["name"] for a in doc["assertions"]], ["checkout", "install-3.12"])

    def test_failing_assertion_stops_and_reads_as_a_failure(self):
        result, path = new_result()
        result.check("checkout", True)
        with self.assertRaises(Stop):
            result.check("install-3.12", False, "install failed")
        self.assertEqual(result.finish(), 1)
        doc = on_disk(path)
        self.assertTrue(doc["complete"])
        self.assertFalse(doc["passed"])


class FinishedRun(unittest.TestCase):
    def test_finish_records_the_pass(self):
        result, path = new_result()
        result.check("checkout", True)
        self.assertEqual(result.finish(), 0)
        doc = on_disk(path)
        self.assertTrue(doc["complete"])
        self.assertTrue(doc["passed"])

    def test_finish_with_no_assertions_is_not_a_pass(self):
        result, path = new_result()
        self.assertEqual(result.finish(), 1)
        self.assertFalse(on_disk(path)["passed"])

    def test_crash_is_complete_and_failed(self):
        result, path = new_result()
        result.check("checkout", True)
        self.assertEqual(result.crash(RuntimeError("boom")), 1)
        doc = on_disk(path)
        self.assertTrue(doc["complete"])
        self.assertFalse(doc["passed"])
        self.assertEqual(doc["assertions"][-1]["name"], "harness")


if __name__ == "__main__":
    unittest.main()
