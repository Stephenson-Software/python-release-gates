"""Unit tests for the text helpers in `gates/common.py`: tail, pytest_summary, env_list, pip_spec."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gates"))
from common import TAIL, env_list, git_url, pip_spec, pytest_summary, tail  # noqa: E402


class Tail(unittest.TestCase):
    def test_short_output_is_kept_whole_and_stripped(self):
        self.assertEqual(tail("\n  error: no such file  \n"), "error: no such file")

    def test_output_of_exactly_the_limit_is_not_cut(self):
        text = "x" * TAIL
        self.assertEqual(tail(text), text)

    def test_long_output_keeps_the_end_behind_an_ellipsis(self):
        text = "head-" + "x" * TAIL + "-END"
        got = tail(text)
        self.assertTrue(got.startswith("…"))
        self.assertTrue(got.endswith("-END"))
        self.assertNotIn("head-", got)
        self.assertEqual(len(got), TAIL + 1)


class PytestSummary(unittest.TestCase):
    def test_reads_the_final_counts_line_without_its_rule(self):
        out = ("============================= test session starts ==============================\n"
               "collected 3 items\n\n"
               "tests/test_a.py ...                                                      [100%]\n\n"
               "============================== 3 passed in 0.12s ===============================\n")
        self.assertEqual(pytest_summary(out), "3 passed in 0.12s")

    def test_a_failing_run_reports_failed_and_passed_counts(self):
        out = ("=================================== FAILURES ===================================\n"
               "___________________________________ test_b ___________________________________\n"
               "E   assert 1 == 2\n"
               "=========================== short test summary info ============================\n"
               "FAILED tests/test_a.py::test_b - assert 1 == 2\n"
               "========================= 1 failed, 2 passed in 0.20s ==========================\n")
        self.assertEqual(pytest_summary(out), "1 failed, 2 passed in 0.20s")

    def test_collection_errors_and_empty_runs_are_summaries(self):
        self.assertEqual(pytest_summary("=== 2 errors in 0.05s ===\n"), "2 errors in 0.05s")
        self.assertEqual(pytest_summary("=== no tests ran in 0.01s ===\n"), "no tests ran in 0.01s")

    def test_a_counts_line_not_framed_by_rules_is_not_taken(self):
        out = "=== 1 passed in 0.1s ===\nsome later line mentioning 5 passed\n"
        self.assertEqual(pytest_summary(out), "1 passed in 0.1s")

    def test_falls_back_to_the_last_non_blank_line(self):
        self.assertEqual(pytest_summary("Ran 4 tests in 0.002s\n\nOK\n\n"), "OK")

    def test_no_output_at_all(self):
        self.assertEqual(pytest_summary("  \n\n"), "(no output)")


class EnvList(unittest.TestCase):
    def read(self, value):
        with mock.patch.dict(os.environ, {"PRG_TEST_LIST": value}):
            return env_list("PRG_TEST_LIST")

    def test_newline_and_comma_separated_items_are_split_and_stripped(self):
        self.assertEqual(self.read(" 3.11 ,3.12\n\n  3.13 \n,"), ["3.11", "3.12", "3.13"])

    def test_an_item_keeps_its_inner_spaces(self):
        self.assertEqual(self.read("owner/app extra-pkg\nowner/other"), ["owner/app extra-pkg", "owner/other"])

    def test_unset_or_blank_is_an_empty_list(self):
        self.assertEqual(self.read(" \n , \n"), [])
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(env_list("PRG_TEST_LIST"), [])


class PipSpec(unittest.TestCase):
    def test_spec_is_a_pep_508_direct_reference_at_the_ref(self):
        self.assertEqual(git_url("owner/lib"), "https://github.com/owner/lib")
        self.assertEqual(pip_spec("lib", "owner/lib", "v1.2.0"), "lib @ git+https://github.com/owner/lib@v1.2.0")


if __name__ == "__main__":
    unittest.main()
