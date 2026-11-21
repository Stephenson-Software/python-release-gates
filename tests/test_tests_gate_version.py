"""Unit tests for `declared_version` in `gates/tests_gate.py`: the `[project] version` the
`version` assertion compares with EXPECTED_VERSION."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gates"))
from tests_gate import declared_version, version_is_dynamic  # noqa: E402


def src_with(pyproject=None):
    """A throwaway source tree, holding `pyproject` as pyproject.toml when given."""
    root = Path(tempfile.mkdtemp())
    if pyproject is not None:
        (root / "pyproject.toml").write_text(pyproject)
    return root


class DeclaredVersion(unittest.TestCase):
    def test_no_pyproject_declares_nothing(self):
        self.assertEqual(declared_version(src_with()), "")

    def test_double_quoted_version_in_project_table(self):
        src = src_with('[project]\nname = "tak"\nversion = "0.3.1"\n')
        self.assertEqual(declared_version(src), "0.3.1")

    def test_single_quoted_version_without_spaces(self):
        src = src_with("[project]\nname = 'tak'\nversion='2.0.0rc1'\n")
        self.assertEqual(declared_version(src), "2.0.0rc1")

    def test_trailing_comment_is_not_part_of_the_version(self):
        src = src_with('[project]\nversion = "1.4.0"  # bumped for release\n')
        self.assertEqual(declared_version(src), "1.4.0")

    def test_dynamic_version_declares_nothing(self):
        src = src_with('[project]\nname = "tak"\ndynamic = ["version"]\n\n'
                       '[tool.setuptools.dynamic]\nversion = {attr = "tak.__version__"}\n')
        self.assertEqual(declared_version(src), "")

    def test_version_in_another_table_is_not_read(self):
        src = src_with('[tool.poetry]\nname = "tak"\nversion = "9.9.9"\n')
        self.assertEqual(declared_version(src), "")

    def test_project_table_after_other_tables_is_found(self):
        src = src_with('[build-system]\nrequires = ["setuptools"]\n\n'
                       '[tool.poetry]\nversion = "9.9.9"\n\n'
                       '[project]\nname = "tak"\nversion = "0.2.0"\n')
        self.assertEqual(declared_version(src), "0.2.0")

    def test_version_after_a_project_subtable_is_not_the_project_version(self):
        src = src_with('[project]\nname = "tak"\ndynamic = ["version"]\n\n'
                       '[project.urls]\nHomepage = "https://example.org"\nversion = "7.7.7"\n')
        self.assertEqual(declared_version(src), "")

    def test_key_that_only_starts_with_version_is_not_read(self):
        src = src_with('[project]\nname = "tak"\nversion_scheme = "x"\nversion = "1.0.0"\n')
        self.assertEqual(declared_version(src), "1.0.0")

    def test_indented_version_key_is_read(self):
        src = src_with('[project]\n  name = "tak"\n  version = "1.0.0"\n')
        self.assertEqual(declared_version(src), "1.0.0")

    def test_comment_after_project_header_is_allowed(self):
        src = src_with('[project]  # package metadata\nname = "tak"\nversion = "1.0.0"\n')
        self.assertEqual(declared_version(src), "1.0.0")

    def test_commented_out_version_is_not_read(self):
        src = src_with('[project]\nname = "tak"\n# version = "6.6.6"\ndynamic = ["version"]\n')
        self.assertEqual(declared_version(src), "")


class VersionIsDynamic(unittest.TestCase):
    def test_dynamic_version_from_an_attribute(self):
        src = src_with('[project]\nname = "graphik"\ndynamic = ["version"]\n\n'
                       '[tool.setuptools.dynamic]\nversion = { attr = "preponderous.graphik._version.__version__" }\n')
        self.assertTrue(version_is_dynamic(src))

    def test_other_dynamic_fields_alone_are_not_a_dynamic_version(self):
        src = src_with('[project]\nname = "x"\nversion = "1.0.0"\ndynamic = ["readme", "dependencies"]\n')
        self.assertFalse(version_is_dynamic(src))

    def test_single_quotes_and_several_fields(self):
        src = src_with("[project]\nname = 'x'\ndynamic = ['readme', 'version']\n")
        self.assertTrue(version_is_dynamic(src))

    def test_static_version_is_not_dynamic(self):
        self.assertFalse(version_is_dynamic(src_with('[project]\nname = "tak"\nversion = "0.3.1"\n')))

    def test_dynamic_outside_the_project_table_is_not_read(self):
        src = src_with('[project]\nname = "x"\n\n[tool.other]\ndynamic = ["version"]\n')
        self.assertFalse(version_is_dynamic(src))

    def test_no_pyproject_is_not_dynamic(self):
        self.assertFalse(version_is_dynamic(src_with()))


if __name__ == "__main__":
    unittest.main()
