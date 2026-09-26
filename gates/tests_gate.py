"""Tests gate: the library's own test suite at the candidate commit, on every interpreter it
supports, installed the way a consumer installs it.

Assertions, in order — the run stops at the first failure:

  checkout            the candidate commit is checked out (infrastructure if not)
  version             the version the build file declares equals EXPECTED_VERSION (when given)
  install-<py>        `pip install <install spec>` succeeds on interpreter <py>
  import-<py>         the package imports from the installed copy and, when it defines
                      `__version__`, reports EXPECTED_VERSION
  tests-<py>          TEST_COMMAND exits 0 (pytest's summary line is quoted)

Environment (set by the workflow from its inputs): REPOSITORY, SHA, PACKAGE, PYTHON_VERSIONS,
INSTALL_EXTRAS, TEST_COMMAND, EXPECTED_VERSION, WORK, RESULT_PATH.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Result, Stop, env_list, make_venv, pip_install, pytest_summary, run, tail  # noqa: E402


def declared_version(src: Path) -> str:
    """`[project] version` from pyproject.toml ('' when dynamic or absent)."""
    text = (src / "pyproject.toml").read_text() if (src / "pyproject.toml").exists() else ""
    m = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if not m:
        return ""
    v = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', m.group(1), re.M)
    return v.group(1) if v else ""


def main() -> int:
    repository, sha = os.environ["REPOSITORY"], os.environ["SHA"]
    work = Path(os.environ.get("WORK", "work")).resolve()
    result = Result("tests", repository, sha, os.environ.get("RESULT_PATH", str(work / "result.json")))
    package = os.environ.get("PACKAGE", "")
    expected = os.environ.get("EXPECTED_VERSION", "").strip()
    extras = os.environ.get("INSTALL_EXTRAS", "dev").strip()
    test_command = os.environ.get("TEST_COMMAND", "").strip() or "python -m pytest -q"
    pythons = env_list("PYTHON_VERSIONS") or ["3.12"]
    result.doc.update({"package": package, "pythonVersions": pythons, "tests": {}})
    src = work / "src"
    try:
        head = run(["git", "-C", str(src), "rev-parse", "HEAD"])[1].strip()
        result.check("checkout", head == sha, f"checked out {head}")
        declared = declared_version(src)
        result.doc["version"] = declared
        if expected:
            result.check("version", declared == expected, f"pyproject.toml declares {declared or '(none)'}; expected {expected}")
        for py in pythons:
            interp = make_venv(work / f"venv-{py}", f"python{py}")
            spec = f".[{extras}]" if extras else "."
            code, out = pip_install(interp, [spec, "pytest"], cwd=src)
            result.check(f"install-{py}", code == 0, f"pip install {spec}" + ("" if code == 0 else f": {tail(out)}"))
            if package:
                probe = (f"import {package}, os; "
                         f"print(os.path.dirname({package}.__file__)); "
                         f"print(getattr({package}, '__version__', ''))")
                code, out = run([str(interp), "-c", probe], cwd=work)
                lines = out.strip().splitlines()
                where, ver = (lines[-2], lines[-1]) if len(lines) >= 2 else ("", "")
                ok = code == 0 and "site-packages" in where and (not expected or not ver or ver == expected)
                detail = (f"{package} {ver or '(no __version__)'} from {where}" if code == 0
                          else f"import failed: {tail(out)}")
                if code == 0 and expected and ver and ver != expected:
                    detail += f"; expected {expected}"
                result.check(f"import-{py}", ok, detail)
            # The suite runs against the INSTALLED copy where the project allows it; a
            # `pythonpath = ["src"]` pytest setting points it back at the checkout, which is
            # the same bytes at the same commit.
            argv = shlex.split(test_command)
            if argv and argv[0] in ("python", "python3"):
                argv[0] = str(interp)
            env = dict(os.environ, PATH=f"{interp.parent}{os.pathsep}{os.environ.get('PATH', '')}")
            code, out = run(argv, cwd=src, env=env, timeout=3600)
            summary = pytest_summary(out)
            result.doc["tests"][py] = {"exit": code, "summary": summary}
            result.check(f"tests-{py}", code == 0, summary if code == 0 else f"exit {code}: {summary}\n{tail(out)}")
    except Stop:
        pass
    except Exception as e:  # noqa: BLE001
        return result.crash(e)
    return result.finish()


if __name__ == "__main__":
    sys.exit(main())
