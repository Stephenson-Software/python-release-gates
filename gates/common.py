"""Shared harness pieces: the result document, subprocess steps, virtualenvs.

Every gate writes one `result.json` — `{gate, repository, sha, candidateSha, passed,
assertions: [{name, passed, detail}], ...}` — and stops at the first assertion that does not
hold. The file is written on every outcome, including a harness crash, so a caller never has
to parse a log.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import venv
from pathlib import Path
from typing import Optional

TAIL = 1500  # characters of a failing step's output quoted in its assertion


class Stop(Exception):
    """Raised after the first failing assertion; the result is already recorded."""


class Result:
    def __init__(self, gate: str, repository: str, sha: str, path: str):
        self.path = Path(path)
        self.doc = {"gate": gate, "repository": repository, "sha": sha, "candidateSha": sha,
                    "passed": False, "assertions": []}

    def check(self, name: str, ok: bool, detail: str = "", stop: bool = True) -> bool:
        self.doc["assertions"].append({"name": name, "passed": bool(ok), "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)
        self.write()
        if not ok and stop:
            raise Stop(name)
        return ok

    def write(self) -> None:
        self.doc["passed"] = bool(self.doc["assertions"]) and all(a["passed"] for a in self.doc["assertions"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.doc, indent=2) + "\n")

    def finish(self) -> int:
        self.write()
        return 0 if self.doc["passed"] else 1

    def crash(self, e: BaseException) -> int:
        """A harness bug or an unusable environment: never a verdict on the candidate."""
        self.doc["assertions"].append({"name": "harness", "passed": False, "detail": f"{type(e).__name__}: {e}"})
        self.write()
        return 1


def run(argv: list[str], cwd: Optional[Path] = None, env: Optional[dict] = None,
        timeout: int = 1800) -> tuple[int, str]:
    """Run a step, echoing it; returns (exit code, combined output)."""
    print(f"$ {' '.join(argv)}" + (f"  (in {cwd})" if cwd else ""), flush=True)
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    out = (proc.stdout or "") + (proc.stderr or "")
    sys.stdout.write(out[-20000:])
    sys.stdout.flush()
    return proc.returncode, out


def pip_install(interp: Path, args: list[str], cwd: Optional[Path] = None) -> tuple[int, str]:
    """`pip install` with one retry: an index hiccup is not a verdict on anything."""
    code, out = run([str(interp), "-m", "pip", "install", "-q", *args], cwd=cwd)
    if code != 0:
        print("pip install failed; retrying once", flush=True)
        code, out = run([str(interp), "-m", "pip", "install", "-q", *args], cwd=cwd)
    return code, out


def tail(text: str) -> str:
    text = text.strip()
    return text if len(text) <= TAIL else "…" + text[-TAIL:]


def pytest_summary(output: str) -> str:
    """The last `=== N passed … ===` line pytest prints (or the last line at all)."""
    lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
    for line in reversed(lines):
        if (" passed" in line or " failed" in line or " error" in line or "no tests ran" in line) and line.startswith("="):
            return line.strip("= ")
    return lines[-1] if lines else "(no output)"


def make_venv(path: Path, python: str = sys.executable) -> Path:
    """A fresh virtualenv built by `python`; returns its interpreter."""
    if python == sys.executable:
        venv.EnvBuilder(with_pip=True, clear=True).create(path)
    else:
        code, out = run([python, "-m", "venv", "--clear", str(path)])
        if code != 0:
            raise RuntimeError(f"{python} -m venv failed: {tail(out)}")
    interp = path / ("Scripts" if os.name == "nt" else "bin") / "python"
    run([str(interp), "-m", "pip", "install", "-q", "--upgrade", "pip"])
    return interp


def git_url(repository: str) -> str:
    return f"https://github.com/{repository}"


def pip_spec(distribution: str, repository: str, ref: str) -> str:
    return f"{distribution} @ git+{git_url(repository)}@{ref}"


def installed_commit(interp: Path, distribution: str) -> Optional[str]:
    """The commit pip recorded for a VCS install (PEP 610 direct_url.json), or None."""
    code, out = run([str(interp), "-c",
                     "import json,sys\n"
                     "from importlib import metadata\n"
                     f"d = metadata.distribution({distribution!r})\n"
                     "t = d.read_text('direct_url.json')\n"
                     "print(json.loads(t).get('vcs_info', {}).get('commit_id', '') if t else '')"])
    commit = out.strip().splitlines()[-1] if code == 0 and out.strip() else ""
    return commit or None


def env_list(name: str) -> list[str]:
    """A newline- or comma-separated workflow input as a list of non-empty stripped items."""
    raw = os.environ.get(name, "")
    items = []
    for line in raw.replace(",", "\n").splitlines():
        line = line.strip()
        if line:
            items.append(line)
    return items
