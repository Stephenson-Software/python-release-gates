"""Unit tests for reading a scenario's view: stderr after the JSON line is not the view (#18)."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gates"))
import save_compat  # noqa: E402


class FakeSide:
    """Stands in for a Side: `view()` only calls `run`, here with a canned result."""

    def __init__(self, code, output):
        self.code, self.output = code, output

    def run(self, argv, extra_env=None):
        return self.code, self.output


def scenario_output(source):
    """Stdout + stderr of a real child process, in the order `common.run()` joins them."""
    script = Path(tempfile.mkdtemp()) / "s.py"
    script.write_text(source)
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=dict(os.environ))
    return proc.stdout + proc.stderr


class ParseView(unittest.TestCase):
    def test_a_plain_view(self):
        self.assertEqual(save_compat.parse_view('{"saves": []}\n'), {"saves": []})

    def test_stderr_after_the_view_is_not_read_as_the_view(self):
        out = scenario_output('import logging, json\nprint(json.dumps({"saves": [1]}), flush=True)\n'
                              'logging.warning("a library warning")\n')
        self.assertTrue(out.rstrip().endswith("a library warning"))
        self.assertEqual(save_compat.parse_view(out), {"saves": [1]})

    def test_lines_before_the_view_are_ignored(self):
        self.assertEqual(save_compat.parse_view('loading\n{"saves": [2]}\n'), {"saves": [2]})

    def test_the_last_object_wins(self):
        self.assertEqual(save_compat.parse_view('{"saves": [1]}\n{"saves": [2]}\n'), {"saves": [2]})

    def test_a_line_that_parses_but_is_no_object_is_passed_over(self):
        self.assertEqual(save_compat.parse_view('{"saves": []}\n3\n"text"\n[1]\nnull\n'), {"saves": []})

    def test_no_object_at_all_is_no_view(self):
        for out in ("", "\n\n", "a warning\n", "[1, 2]\n", "null\n", "{not json\n"):
            with self.subTest(out=out):
                self.assertIsNone(save_compat.parse_view(out))


class View(unittest.TestCase):
    def read(self, code, output):
        return save_compat.view(FakeSide(code, output), Path("s.py"), Path("saves"))

    def test_a_clean_read_with_a_warning_after_it_passes(self):
        out = '{"saves": []}\na library warning\n'
        self.assertEqual(self.read(0, out), (0, {"saves": []}, out))

    def test_a_scenario_that_printed_no_object_fails(self):
        self.assertEqual(self.read(0, "[1, 2]\n"), (1, None, "[1, 2]\n"))
        self.assertEqual(self.read(0, ""), (1, None, ""))

    def test_a_failed_scenario_keeps_its_exit_code_even_with_a_view_printed(self):
        self.assertEqual(self.read(3, '{"saves": []}\nTraceback\n'), (3, None, '{"saves": []}\nTraceback\n'))


if __name__ == "__main__":
    unittest.main()
