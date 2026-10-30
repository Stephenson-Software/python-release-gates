"""Unit tests for the tests gate's import probe: a package without __version__ still reports
where it imported from (#15)."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gates"))
from tests_gate import import_probe, parse_probe  # noqa: E402


def probe(init_source: str, name: str = "prgprobepkg") -> tuple[Path, str]:
    """Run the gate's probe against a throwaway package; returns (its directory, stdout + stderr
    in the order `common.run()` joins them)."""
    root = Path(tempfile.mkdtemp())
    pkg = root / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(init_source)
    proc = subprocess.run([sys.executable, "-c", import_probe(name)], cwd=root, capture_output=True,
                          text=True, env=dict(os.environ, PYTHONPATH=str(root)))
    return pkg, proc.stdout + proc.stderr


class ImportProbe(unittest.TestCase):
    def test_package_without_version_reports_its_directory(self):
        pkg, out = probe("")
        self.assertEqual(parse_probe(out), (str(pkg), ""))

    def test_package_with_version_reports_both(self):
        pkg, out = probe("__version__ = '1.2.3'\n")
        self.assertEqual(parse_probe(out), (str(pkg), "1.2.3"))

    def test_stderr_after_the_probe_line_is_not_read_as_the_result(self):
        pkg, out = probe("import sys\nsys.stderr.write('a warning\\nanother line\\n')\n")
        self.assertTrue(out.rstrip().endswith("another line"))
        self.assertEqual(parse_probe(out), (str(pkg), ""))

    def test_output_without_a_probe_line_is_empty(self):
        self.assertEqual(parse_probe("Traceback (most recent call last):\nImportError: nope\n"), ("", ""))
        self.assertEqual(parse_probe(""), ("", ""))


if __name__ == "__main__":
    unittest.main()
