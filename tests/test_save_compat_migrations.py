"""Unit tests for the save-compatibility gate's declared migrations (files-kept-3)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gates"))
import save_compat  # noqa: E402

FISHE = {"player.json": "slot_1/player.json", "stats.json": "slot_1/stats.json"}


def scenario(text):
    d = tempfile.mkdtemp()
    p = Path(d) / "s.py"
    p.write_text(text)
    return p


class DeclaredMigrations(unittest.TestCase):
    def test_absent_is_empty(self):
        self.assertEqual(save_compat.declared_migrations(scenario("X = 1\n")), {})

    def test_read_statically_never_run(self):
        p = scenario('raise SystemExit("never run")\nSAVE_MIGRATIONS = {"a.json": "slot_1/a.json"}\n')
        self.assertEqual(save_compat.declared_migrations(p), {"a.json": "slot_1/a.json"})

    def test_the_fishe_scenario_declares_its_move(self):
        p = Path(__file__).resolve().parent.parent / "scenarios" / "fishe_saves.py"
        got = save_compat.declared_migrations(p)
        self.assertEqual(got, {"player.json": "slot_1/player.json", "stats.json": "slot_1/stats.json",
                               "timeService.json": "slot_1/timeService.json"})

    def test_unsafe_or_odd_declarations_are_refused(self):
        for text in ('SAVE_MIGRATIONS = ["a"]\n', 'SAVE_MIGRATIONS = {"a": "../b"}\n',
                     'SAVE_MIGRATIONS = {"/a": "b"}\n', 'SAVE_MIGRATIONS = {"a": "a"}\n',
                     'SAVE_MIGRATIONS = {"a": "c", "b": "c"}\n', 'SAVE_MIGRATIONS = {"a": f()}\n',
                     'SAVE_MIGRATIONS = {"a": ""}\n'):
            with self.subTest(text=text), self.assertRaises(save_compat.MigrationDeclarationError):
                save_compat.declared_migrations(scenario(text))


class KeptAfterAppend(unittest.TestCase):
    written = {"player.json": "p", "stats.json": "s", "provenance.json": "v"}

    def test_a_declared_byte_identical_move_passes(self):
        after = {"provenance.json": "v", "slot_1": "dir", "slot_1/player.json": "p", "slot_1/stats.json": "s",
                 "slot_2": "dir", "slot_2/player.json": "n"}
        problems, moves = save_compat.kept_after_append(self.written, after, FISHE)
        self.assertEqual(problems, [])
        self.assertEqual(moves, ["player.json -> slot_1/player.json", "stats.json -> slot_1/stats.json"])

    def test_a_move_that_changes_bytes_blocks(self):
        after = {"provenance.json": "v", "slot_1/player.json": "CHANGED", "slot_1/stats.json": "s"}
        problems, _ = save_compat.kept_after_append(self.written, after, FISHE)
        self.assertEqual(problems, ["removed player.json (declared to move to slot_1/player.json, which holds "
                                    "different bytes)"])

    def test_a_move_whose_destination_is_missing_blocks(self):
        after = {"provenance.json": "v", "slot_1/stats.json": "s"}
        problems, _ = save_compat.kept_after_append(self.written, after, FISHE)
        self.assertIn("removed player.json (declared to move to slot_1/player.json, which is missing)", problems)

    def test_an_undeclared_removal_or_any_change_still_blocks(self):
        after = {"slot_1/player.json": "p", "slot_1/stats.json": "s"}
        problems, _ = save_compat.kept_after_append(self.written, after, FISHE)
        self.assertEqual(problems, ["removed provenance.json"])
        after = {"player.json": "p", "stats.json": "s2", "provenance.json": "v"}
        problems, _ = save_compat.kept_after_append(self.written, after, FISHE)
        self.assertEqual(problems, ["changed stats.json"])

    def test_without_a_declaration_a_move_blocks_as_before(self):
        after = {"provenance.json": "v", "slot_1/player.json": "p", "slot_1/stats.json": "s"}
        problems, moves = save_compat.kept_after_append(self.written, after, {})
        self.assertEqual(problems, ["removed player.json", "removed stats.json"])
        self.assertEqual(moves, [])

    def test_a_destination_that_already_existed_is_not_a_move(self):
        written = dict(self.written, **{"slot_1/player.json": "p"})
        after = {"stats.json": "s", "provenance.json": "v", "slot_1/player.json": "p"}
        problems, _ = save_compat.kept_after_append(written, after, FISHE)
        self.assertEqual(problems, ["removed player.json (declared to move to slot_1/player.json, which existed "
                                    "before)"])

    def test_no_move_at_all_passes(self):
        after = dict(self.written, **{"slot_1/player.json": "n"})
        self.assertEqual(save_compat.kept_after_append(self.written, after, FISHE), ([], []))


if __name__ == "__main__":
    unittest.main()
