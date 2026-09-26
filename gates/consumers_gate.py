"""Consumers gate: every project that pins the library runs its own test suite twice — first
with the version it pins today (the control phase), then with the candidate commit swapped in.

A consumer that passes on its current pin and fails on the candidate is a REGRESSION and fails
the gate. A consumer that already fails on its current pin is PRE-EXISTING: reported, never
held against the candidate. A consumer whose requirements do not name the library is SKIPPED.

Assertions per consumer <Name> (the run stops at the first failure):

  control             the consumer could be cloned and installed at all (infrastructure if not)
  pin-<Name>          the library requirement line the consumer carries (informational)
  control-<Name>      the consumer's suite on its current pin — always passes; the detail says
                      whether the suite was green (a red one marks the consumer pre-existing)
  install-<Name>      the candidate commit installs over the consumer's environment, and pip
                      records exactly that commit
  candidate-<Name>    the consumer's suite on the candidate: must pass unless pre-existing

Environment: REPOSITORY, SHA, DISTRIBUTION, CONSUMERS (lines `owner/repo [extra pip package…]`),
PYTHON_VERSION, TEST_COMMAND, WORK, RESULT_PATH.
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


def requirement_line(consumer: Path, repository: str, distribution: str) -> str:
    """The requirements.txt line that installs the library, or ''."""
    req = consumer / "requirements.txt"
    if not req.exists():
        return ""
    url = git_url(repository).lower()
    for line in req.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        name = re.split(r"[\s<>=!~@\[;]", s, 1)[0].lower()
        if url in s.lower() or name == distribution.lower():
            return s
    return ""


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
            pin = requirement_line(dest, repository, distribution)
            entry["pin"] = pin or None
            if not pin:
                entry["skipped"] = f"requirements.txt does not name {distribution}"
                result.check(f"pin-{name}", True, f"SKIPPED — {entry['skipped']}")
                continue
            result.check(f"pin-{name}", True, f"{repo} @ {entry['sha'][:10]} pins `{pin}`")

            interp = make_venv(work / f"venv-{name}", f"python{python}")
            base = ["pytest", "pytest-cov", *extras]
            args = base + (["-r", "requirements.txt"] if (dest / "requirements.txt").exists() else [])
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
