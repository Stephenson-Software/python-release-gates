"""Consumers gate: every project that pins the library runs its own test suite twice — first
with the version it pins today (the control phase), then with the candidate commit swapped in.

A consumer that passes on its current pin and fails on the candidate is a REGRESSION and fails
the gate. A consumer that already fails on its current pin is PRE-EXISTING: reported, never
held against the candidate. A consumer whose requirements do not name the library (find_pin; PIN_SOURCES `all` also
reads requirements*.txt and pyproject.toml dependencies) is SKIPPED.

Assertions per consumer <Name> (the run stops at the first failure):

  control             the consumer could be cloned and installed at all (infrastructure if not)
  pin-<Name>          the library requirement line the consumer carries (informational)
  control-<Name>      the consumer's suite on its current pin — always passes; the detail says
                      whether the suite was green (a red one marks the consumer pre-existing)
  install-<Name>      the candidate commit installs over the consumer's environment, and pip
                      records exactly that commit
  candidate-<Name>    the consumer's suite on the candidate: must pass unless pre-existing

Environment: REPOSITORY, SHA, DISTRIBUTION, CONSUMERS (lines `owner/repo [extra pip package…]`),
PYTHON_VERSION, TEST_COMMAND, PIN_SOURCES (`requirements` | `all`, see find_pin), WORK, RESULT_PATH.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (Result, Stop, env_list, git_url, installed_commit, make_venv, pip_install,  # noqa: E402
                    pip_spec, pytest_summary, run, tail)


def _names_library(spec: str, url: str, distribution: str) -> bool:
    s = spec.strip()
    if not s or s.startswith(("#", "-")):
        return False
    name = re.split(r"[\s<>=!~@\[;]", s, 1)[0].lower().replace("_", "-")
    return url in s.lower() or name == distribution.lower().replace("_", "-")


def find_pin(consumer: Path, repository: str, distribution: str,
             sources: str = "requirements") -> tuple[str, str, list[str]]:
    """Where the consumer pins the library: (the requirement line, where it was found, the pip
    arguments that install the consumer the way it installs itself), or ("", "", []).

    PIN_SOURCES `requirements` (the default, the v1/v2 behaviour): requirements.txt only.
    PIN_SOURCES `all` (v3): in order, requirements.txt; any other requirements*.txt (apex and ophidian
    keep optional recording dependencies in requirements-record.txt); then pyproject.toml's
    [project] dependencies and optional-dependencies (viron-usd, usd-web's `dev` extra). A pin
    in a side requirements file installs requirements.txt too, when there is one; a pin in
    pyproject.toml installs the consumer itself, editable, with the extra that carries it."""
    url = git_url(repository).lower()
    files = [consumer / "requirements.txt"]
    if sources == "all":
        files += sorted(f for f in consumer.glob("requirements*.txt") if f.name != "requirements.txt")
    for req in files:
        if not req.is_file():
            continue
        for line in req.read_text().splitlines():
            if _names_library(line, url, distribution):
                args = ["-r", "requirements.txt"] if (consumer / "requirements.txt").is_file() else []
                if req.name != "requirements.txt":
                    args += ["-r", req.name]
                return line.strip(), req.name, args
    pyproject = consumer / "pyproject.toml"
    if sources == "all" and pyproject.is_file():
        import tomllib
        project = tomllib.loads(pyproject.read_text()).get("project") or {}
        for dep in project.get("dependencies") or []:
            if _names_library(dep, url, distribution):
                return dep.strip(), "pyproject.toml [project] dependencies", ["-e", "."]
        for extra, deps in sorted((project.get("optional-dependencies") or {}).items()):
            for dep in deps:
                if _names_library(dep, url, distribution):
                    return dep.strip(), f"pyproject.toml [{extra}] extra", ["-e", f".[{extra}]"]
    return "", "", []


def requirement_line(consumer: Path, repository: str, distribution: str, sources: str = "requirements") -> str:
    """The line that installs the library, wherever find_pin finds it, or ''."""
    return find_pin(consumer, repository, distribution, sources)[0]


def suite(interp: Path, command: str, cwd: Path) -> tuple[int, str]:
    argv = shlex.split(command)
    if argv and argv[0] in ("python", "python3"):
        argv[0] = str(interp)
    env = dict(os.environ, PATH=f"{interp.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    return run(argv, cwd=cwd, env=env, timeout=3600)


def main() -> int:
    repository, sha = os.environ["REPOSITORY"], os.environ["SHA"]
    distribution = os.environ.get("DISTRIBUTION", "").strip() or repository.rsplit("/", 1)[-1]
    work = Path(os.environ.get("WORK", "work")).resolve()
    result = Result("consumers", repository, sha, os.environ.get("RESULT_PATH", str(work / "result.json")))
    python = os.environ.get("PYTHON_VERSION", "").strip() or "3.12"
    command = os.environ.get("TEST_COMMAND", "").strip() or "python -m pytest -q"
    sources = os.environ.get("PIN_SOURCES", "").strip() or "requirements"
    if sources not in ("requirements", "all"):
        raise SystemExit(f"PIN_SOURCES must be `requirements` or `all`, not {sources!r}")
    consumers = []
    for line in env_list("CONSUMERS"):
        parts = line.split()
        consumers.append((parts[0], parts[1:]))
    result.doc.update({"distribution": distribution, "pythonVersion": python, "consumers": [], "preExisting": []})
    try:
        if not consumers:
            result.check("control", False, "no consumers given")
        for repo, extras in consumers:
            name = repo.rsplit("/", 1)[-1]
            entry = {"repository": repo, "name": name, "sha": None, "pin": None, "pre_existing": False,
                     "skipped": None, "control": None, "candidate": None}
            result.doc["consumers"].append(entry)
            dest = work / "consumers" / name
            code, out = run(["git", "clone", "-q", "--depth", "1", git_url(repo), str(dest)])
            if code != 0:
                result.check("control", False, f"{repo}: clone failed: {tail(out)}")
            entry["sha"] = run(["git", "-C", str(dest), "rev-parse", "HEAD"])[1].strip()
            pin, where, install_args = find_pin(dest, repository, distribution, sources)
            entry["pin"] = pin or None
            entry["pinnedIn"] = where or None
            if not pin:
                entry["skipped"] = (f"no requirements*.txt or pyproject.toml dependency names {distribution}" if sources == "all"
                                    else f"requirements.txt does not name {distribution}")
                result.check(f"pin-{name}", True, f"SKIPPED — {entry['skipped']}")
                continue
            result.check(f"pin-{name}", True, f"{repo} @ {entry['sha'][:10]} pins `{pin}` ({where})")

            interp = make_venv(work / f"venv-{name}", f"python{python}")
            base = ["pytest", "pytest-cov", *extras]
            args = base + install_args
            code, out = pip_install(interp, args, cwd=dest)
            if code != 0:
                result.check("control", False, f"{repo}: its own requirements do not install: {tail(out)}")
            code, out = suite(interp, command, dest)
            entry["control"] = {"exit": code, "summary": pytest_summary(out)}
            if code != 0:
                entry["pre_existing"] = True
                result.doc["preExisting"].append(name)
            result.check(f"control-{name}", True,
                         (f"green on its current pin: {entry['control']['summary']}" if code == 0 else
                          f"ALREADY RED on its current pin (pre-existing, not held against the candidate): "
                          f"{entry['control']['summary']}"))

            spec = pip_spec(distribution, repository, sha)
            code, out = pip_install(interp, ["--no-deps", "--force-reinstall", spec])
            if code == 0:
                code, out = pip_install(interp, [spec])  # any dependency the candidate added
            got = installed_commit(interp, distribution) if code == 0 else None
            result.check(f"install-{name}", code == 0 and got == sha,
                         f"{distribution} installed at {got}" if code == 0 else f"install failed: {tail(out)}")

            code, out = suite(interp, command, dest)
            entry["candidate"] = {"exit": code, "summary": pytest_summary(out)}
            if entry["pre_existing"]:
                result.check(f"candidate-{name}", True,
                             f"PRE-EXISTING (also red on its current pin; not a regression) — {entry['candidate']['summary']}")
            else:
                result.check(f"candidate-{name}", code == 0,
                             entry["candidate"]["summary"] if code == 0
                             else f"REGRESSION: green on `{pin}`, red on the candidate — {entry['candidate']['summary']}\n{tail(out)}")
    except Stop:
        pass
    except Exception as e:  # noqa: BLE001
        return result.crash(e)
    return result.finish()


if __name__ == "__main__":
    sys.exit(main())
