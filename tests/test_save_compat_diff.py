"""Unit tests for how the save-compatibility gate describes a view difference (first_difference,
describe_view_diff) and drops fidelity blocks before comparing (strip_fidelity)."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gates"))
import save_compat  # noqa: E402


class FirstDifference(unittest.TestCase):
    def test_a_top_level_key_has_no_leading_dot(self):
        self.assertEqual(save_compat.first_difference({"a": 1, "b": 2}, {"a": 1, "b": 3}),
                         "b: baseline 2 vs candidate 3")

    def test_keys_are_visited_in_sorted_order(self):
        self.assertEqual(save_compat.first_difference({"b": 1, "a": 1}, {"b": 2, "a": 2}),
                         "a: baseline 1 vs candidate 2")

    def test_a_missing_key_reads_as_null(self):
        self.assertEqual(save_compat.first_difference({"a": 1}, {}), "a: baseline 1 vs candidate null")

    def test_nested_dicts_extend_the_given_path(self):
        self.assertEqual(save_compat.first_difference({"x": {"y": 1}}, {"x": {"y": 2}}, "root"),
                         "root.x.y: baseline 1 vs candidate 2")

    def test_a_list_element_is_located_by_index(self):
        self.assertEqual(save_compat.first_difference([1, 2], [1, 3], "l"), "l[1]: baseline 2 vs candidate 3")

    def test_a_dict_element_without_a_name_has_no_name_in_the_path(self):
        self.assertEqual(save_compat.first_difference([{"g": 1}], [{"g": 2}], "s"),
                         "s[0].g: baseline 1 vs candidate 2")

    def test_lists_of_different_length_are_reported_whole(self):
        self.assertEqual(save_compat.first_difference([1], [1, 2], "s"), "s: baseline [1] vs candidate [1, 2]")

    def test_different_types_are_reported_whole(self):
        self.assertEqual(save_compat.first_difference({"a": 1}, [1], "p"),
                         'p: baseline {"a": 1} vs candidate [1]')

    def test_a_null_value_and_an_absent_key_are_reported_at_the_parent(self):
        self.assertEqual(save_compat.first_difference({"a": None}, {}, "p"),
                         'p: baseline {"a": null} vs candidate {}')

    def test_each_side_is_cut_to_300_characters(self):
        self.assertEqual(save_compat.first_difference("x" * 400, 1, "p"),
                         'p: baseline "' + "x" * 299 + " vs candidate 1")


class DescribeViewDiff(unittest.TestCase):
    def test_views_that_are_not_objects_are_not_itemised(self):
        self.assertEqual(save_compat.describe_view_diff([1], [2]), "views differ")

    def test_a_null_value_and_an_absent_key_are_not_itemised(self):
        self.assertEqual(save_compat.describe_view_diff({"a": None}, {}), "views differ")

    def test_counts_every_differing_field_and_names_the_first(self):
        self.assertEqual(save_compat.describe_view_diff({"a": 1, "b": 2, "c": 3}, {"a": 0, "b": 0, "c": 3}),
                         "2 field(s) differ, first `a`: baseline 1 vs candidate 0")

    def test_a_structured_field_is_located_by_path(self):
        self.assertEqual(save_compat.describe_view_diff({"saves": [1]}, {"saves": [2]}),
                         "1 field(s) differ; first at saves[0]: baseline 1 vs candidate 2")

    def test_a_scalar_baseline_field_is_quoted_whole(self):
        self.assertEqual(save_compat.describe_view_diff({"a": 1}, {"a": {"b": 1}}),
                         '1 field(s) differ, first `a`: baseline 1 vs candidate {"b": 1}')

    def test_each_scalar_side_is_cut_to_600_characters(self):
        self.assertEqual(save_compat.describe_view_diff({"a": "x" * 700}, {"a": 1}),
                         '1 field(s) differ, first `a`: baseline "' + "x" * 599 + " vs candidate 1")


class StripFidelity(unittest.TestCase):
    def test_removes_fidelity_at_every_depth(self):
        v = {"fidelity": {}, "saves": [{"name": "a", "fidelity": {"g": True}, "inv": [{"fidelity": 1, "k": 2}]}]}
        self.assertEqual(save_compat.strip_fidelity(v), {"saves": [{"name": "a", "inv": [{"k": 2}]}]})

    def test_only_the_exact_key_is_removed(self):
        self.assertEqual(save_compat.strip_fidelity({"fidelity_note": 1, "Fidelity": 2}),
                         {"fidelity_note": 1, "Fidelity": 2})

    def test_scalars_pass_through(self):
        for v in (3, "fidelity", None, True):
            self.assertEqual(save_compat.strip_fidelity(v), v)

    def test_the_input_is_not_modified(self):
        v = {"saves": [{"fidelity": {"g": True}, "g": 1}]}
        before = copy.deepcopy(v)
        save_compat.strip_fidelity(v)
        self.assertEqual(v, before)


if __name__ == "__main__":
    unittest.main()
