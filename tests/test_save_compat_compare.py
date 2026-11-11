"""Unit tests for how the save-compatibility gate compares views and files (candidate-read,
candidate-append, fixture-read, fixture-unchanged, fixture-counts)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gates"))
import save_compat  # noqa: E402


def save(name, *, fidelity=None, **fields):
    s = {"name": name, **fields}
    if fidelity is not None:
        s["fidelity"] = fidelity
    return s


class Compare(unittest.TestCase):
    def test_equal_views_pass(self):
        v = {"saves": [save("a", gold=3)], "next_slot": 2}
        ok, why = save_compat.compare(v, dict(v))
        self.assertTrue(ok)
        self.assertEqual(why, "candidate view equals the baseline view")

    def test_a_differing_field_fails_and_names_it(self):
        ok, why = save_compat.compare({"next_slot": 2, "saves": []}, {"next_slot": 3, "saves": []})
        self.assertFalse(ok)
        self.assertEqual(why, "1 field(s) differ, first `next_slot`: baseline 2 vs candidate 3")

    def test_a_nested_difference_is_located_by_path_and_save_name(self):
        ref = {"saves": [save("a", gold=3), save("b", gold=5)]}
        got = {"saves": [save("a", gold=3), save("b", gold=6)]}
        ok, why = save_compat.compare(ref, got)
        self.assertFalse(ok)
        self.assertEqual(why, "1 field(s) differ; first at saves[1 'b'].gold: baseline 5 vs candidate 6")

    def test_a_missing_save_fails(self):
        ok, why = save_compat.compare({"saves": [save("a"), save("b")]}, {"saves": [save("a")]})
        self.assertFalse(ok)
        self.assertIn("first at saves:", why)

    def test_fidelity_blocks_are_not_compared_for_equality(self):
        ref = {"saves": [save("a", gold=3, fidelity={"gold": True})]}
        got = {"saves": [save("a", gold=3, fidelity={"gold": True, "inventory": True})]}
        self.assertEqual(save_compat.compare(ref, got), (True, "candidate view equals the baseline view"))

    def test_a_false_fidelity_value_in_the_candidate_fails(self):
        ref = {"saves": [save("a", fidelity={"gold": True})]}
        got = {"saves": [save("a", fidelity={"gold": False})]}
        ok, why = save_compat.compare(ref, got)
        self.assertFalse(ok)
        self.assertEqual(why, "the candidate does not reproduce what the save files record at 1 place(s): "
                              "['saves[0].gold']")

    def test_a_truthy_but_not_true_fidelity_value_fails(self):
        ok, _ = save_compat.compare({"saves": []}, {"saves": [], "fidelity": {"slots": 1}})
        self.assertFalse(ok)

    def test_a_baseline_fidelity_failure_is_reported_but_passes(self):
        ref = {"saves": [save("a", fidelity={"gold": False})]}
        got = {"saves": [save("a", fidelity={"gold": True})]}
        ok, why = save_compat.compare(ref, got)
        self.assertTrue(ok)
        self.assertIn("the baseline did not reproduce its own files at 1 place(s)", why)


class FidelityFailures(unittest.TestCase):
    def test_none_when_every_value_is_true(self):
        self.assertEqual(save_compat.fidelity_failures({"fidelity": {"a": True}, "x": [{"fidelity": {}}]}), [])

    def test_a_fidelity_that_is_not_an_object_fails(self):
        self.assertEqual(save_compat.fidelity_failures({"saves": [{"fidelity": True}]}),
                         ["saves[0].fidelity (not an object)"])

    def test_top_level_names_have_no_leading_dot(self):
        self.assertEqual(save_compat.fidelity_failures({"fidelity": {"slots": None}}), ["slots"])


class DiffFiles(unittest.TestCase):
    def test_identical_is_empty(self):
        self.assertEqual(save_compat.diff_files({"a": "1", "d": "dir"}, {"a": "1", "d": "dir"}), [])

    def test_removed_added_and_changed_in_name_order(self):
        before = {"a.json": "1", "b.json": "2", "c.json": "3"}
        after = {"b.json": "2", "c.json": "9", "d.json": "4"}
        self.assertEqual(save_compat.diff_files(before, after),
                         ["removed a.json", "changed c.json", "added d.json"])


class Counts(unittest.TestCase):
    def test_counts_saves_unreadable_and_valid(self):
        v = {"saves": [save("a", validation="valid"),
                       save("b", metadata={"unreadable": True}),
                       save("c", validation="invalid", metadata={})],
             "next_slot": 4}
        self.assertEqual(save_compat.counts(v), {"saves": 3, "unreadable": 1, "valid": 1, "next_slot": 4})

    def test_no_view_counts_nothing(self):
        empty = {"saves": 0, "unreadable": 0, "valid": 0, "next_slot": None}
        self.assertEqual(save_compat.counts(None), empty)
        self.assertEqual(save_compat.counts({}), empty)
        self.assertEqual(save_compat.counts([1, 2]), empty)


if __name__ == "__main__":
    unittest.main()
