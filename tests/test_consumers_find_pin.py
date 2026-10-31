"""Unit tests for the consumers gate's pin lookup (find_pin): where a consumer pins the library,
and the pip arguments that install the consumer the way it installs itself."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gates"))
from consumers_gate import find_pin, requirement_line  # noqa: E402

REPO = "Stephenson-Software/tak"
NOTHING = ("", "", [])


def consumer(files=None):
    """A throwaway consumer checkout holding `files` (file name -> text)."""
    root = Path(tempfile.mkdtemp())
    for name, text in (files or {}).items():
        (root / name).write_text(text)
    return root


def requirements(text):
    return consumer({"requirements.txt": text})


class RequirementsTxt(unittest.TestCase):
    def test_a_pin_in_requirements_txt_installs_requirements_txt(self):
        root = requirements("pygame==2.5\n  tak @ git+https://github.com/Stephenson-Software/tak@v0.2.0  \n")
        self.assertEqual(find_pin(root, REPO, "tak"),
                         ("tak @ git+https://github.com/Stephenson-Software/tak@v0.2.0", "requirements.txt",
                          ["-r", "requirements.txt"]))

    def test_the_repository_url_names_the_library_whatever_the_name(self):
        line = "git+https://github.com/stephenson-software/TAK@v0.2.0#egg=tak"
        self.assertEqual(find_pin(requirements(line + "\n"), REPO, "tak")[0], line)

    def test_names_are_compared_case_and_separator_insensitively(self):
        for line in ("Tak_Lib==1.0", "tak-lib>=1; python_version >= '3.10'", "tak_lib[extra]~=1.0"):
            with self.subTest(line=line):
                self.assertEqual(find_pin(requirements(line + "\n"), "o/tak-lib", "tak-lib")[0], line)

    def test_a_name_that_only_starts_with_the_distribution_is_not_a_pin(self):
        self.assertEqual(find_pin(requirements("takeover==1.0\ntak-extras\n"), REPO, "tak"), NOTHING)

    def test_comments_and_option_lines_are_not_pins(self):
        self.assertEqual(find_pin(requirements("# tak==0.2.0\n-r tak.txt\n--index-url x\n\n"), REPO, "tak"),
                         NOTHING)

    def test_no_requirements_at_all_is_no_pin(self):
        self.assertEqual(find_pin(consumer(), REPO, "tak"), NOTHING)

    def test_requirement_line_is_the_pin_line(self):
        root = requirements("tak==0.2.0\n")
        self.assertEqual(requirement_line(root, REPO, "tak"), "tak==0.2.0")
        self.assertEqual(requirement_line(consumer(), REPO, "tak"), "")


class SideRequirementsFiles(unittest.TestCase):
    def test_the_default_sources_read_requirements_txt_only(self):
        root = consumer({"requirements.txt": "pygame\n", "requirements-record.txt": "tak==0.2.0\n"})
        self.assertEqual(find_pin(root, REPO, "tak"), NOTHING)
        self.assertEqual(find_pin(root, REPO, "tak", "requirements"), NOTHING)

    def test_all_finds_a_side_file_and_installs_both_files(self):
        root = consumer({"requirements.txt": "pygame\n", "requirements-record.txt": "tak==0.2.0\n"})
        self.assertEqual(find_pin(root, REPO, "tak", "all"),
                         ("tak==0.2.0", "requirements-record.txt",
                          ["-r", "requirements.txt", "-r", "requirements-record.txt"]))

    def test_a_side_file_without_requirements_txt_installs_the_side_file_only(self):
        root = consumer({"requirements-record.txt": "tak==0.2.0\n"})
        self.assertEqual(find_pin(root, REPO, "tak", "all"),
                         ("tak==0.2.0", "requirements-record.txt", ["-r", "requirements-record.txt"]))

    def test_requirements_txt_wins_over_a_side_file(self):
        root = consumer({"requirements.txt": "tak==0.2.0\n", "requirements-dev.txt": "tak==0.1.0\n"})
        self.assertEqual(find_pin(root, REPO, "tak", "all"),
                         ("tak==0.2.0", "requirements.txt", ["-r", "requirements.txt"]))

    def test_side_files_are_read_in_name_order(self):
        root = consumer({"requirements-record.txt": "tak==0.2.0\n", "requirements-dev.txt": "tak==0.1.0\n"})
        self.assertEqual(find_pin(root, REPO, "tak", "all")[:2], ("tak==0.1.0", "requirements-dev.txt"))


PYPROJECT = """\
[project]
name = "consumer"
dependencies = [{deps}]

[project.optional-dependencies]
{extras}
"""


@unittest.skipIf(sys.version_info < (3, 11), "find_pin reads pyproject.toml with tomllib (3.11+)")
class PyprojectToml(unittest.TestCase):
    def test_a_project_dependency_installs_the_consumer_editable(self):
        root = consumer({"pyproject.toml": PYPROJECT.format(deps='"numpy", "tak>=0.2"', extras="")})
        self.assertEqual(find_pin(root, REPO, "tak", "all"),
                         ("tak>=0.2", "pyproject.toml [project] dependencies", ["-e", "."]))

    def test_an_extra_installs_the_consumer_editable_with_that_extra(self):
        root = consumer({"pyproject.toml": PYPROJECT.format(deps='"numpy"', extras='dev = ["pytest", "tak==0.2.0"]')})
        self.assertEqual(find_pin(root, REPO, "tak", "all"),
                         ("tak==0.2.0", "pyproject.toml [dev] extra", ["-e", ".[dev]"]))

    def test_a_requirements_file_wins_over_pyproject_toml(self):
        root = consumer({"requirements-record.txt": "tak==0.1.0\n",
                         "pyproject.toml": PYPROJECT.format(deps='"tak==0.2.0"', extras="")})
        self.assertEqual(find_pin(root, REPO, "tak", "all")[:2], ("tak==0.1.0", "requirements-record.txt"))

    def test_the_default_sources_ignore_pyproject_toml(self):
        root = consumer({"pyproject.toml": PYPROJECT.format(deps='"tak==0.2.0"', extras="")})
        self.assertEqual(find_pin(root, REPO, "tak"), NOTHING)


if __name__ == "__main__":
    unittest.main()
