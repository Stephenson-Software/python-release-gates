"""Save-compatibility gate: data the baseline release wrote, read by the candidate.

A scenario script (SCENARIO, a path in this repository) drives the library's own save API in
three modes — `write DIR` (lay down saves), `read DIR` (print, as JSON, everything the
library reports about DIR), `append DIR` (add one save next to the existing ones). The gate:

  baseline-install    the baseline ref installs (a baseline failure: nothing to compare with)
  candidate-install   the candidate commit installs, and pip records exactly that commit
  baseline-write      the baseline writes the scenario's saves
  baseline-read       the baseline reads them back — the reference view
  read-1, read-2      the candidate's view of the same directory equals the reference view
  files-kept-1, -2    every file is byte-identical after each read (reading changes nothing)
  append              the candidate adds a save beside the old ones
  files-kept-3        every file the baseline wrote is still byte-identical
  read-3              the candidate still reports every old save exactly as the reference
                      view did, plus the one it added

With FIXTURE_URL (a curated set of saves, a .tar.gz, optionally described by a manifest at
FIXTURE_MANIFEST_URL in the plugin-fixtures shape — `{library, slug, version, scenario,
recordedAt, primaryFile?, expected: {saves, unreadable, valid, next_slot}}`), the same
directory is also read by both versions:

  fixture             the archive (and manifest, when given) downloads and unpacks; a fixture
                      that was asked for and cannot be read BLOCKS — it is never skipped
  fixture-baseline    the baseline reads it — the reference view
  fixture-expected    the reference view carries the manifest's `expected` counts
  fixture-read-1, -2  the candidate's view equals the reference view
  fixture-files-kept-1, -2   every fixture file byte-identical after each read

A view may carry `fidelity` blocks: `{name: true|false}`, each saying whether the version
reproduced what the save FILE records (e.g. every inventory item in the slot the file names).
Those are judged against the file, not against the other version: everything outside them
must be equal, and every fidelity value in the candidate's view must be true. A baseline that
did not reproduce its own file is reported, never held against the candidate; a candidate
that does not is a failure.

Any doubt about data fails the gate. None of these checks can be waived.

INSTALL_MODE `checkout` (v2) is for an application rather than a pip distribution: each
version is a git checkout of the repository at its ref, with its own `requirements.txt`
installed into its own virtualenv. The scenario then runs from that checkout (cwd), with
`<checkout>/<SOURCE_ROOT>` on PYTHONPATH, APP_SOURCE set to the checkout, and SDL's dummy video
and audio drivers. `baseline-install` / `candidate-install` then mean: the ref checks out (and
for the candidate, HEAD is exactly SHA) and its requirements install.

Environment: REPOSITORY, SHA, BASELINE_REF, DISTRIBUTION, SCENARIO, PYTHON_VERSION,
FIXTURE_URL, FIXTURE_MANIFEST_URL, INSTALL_MODE (pip | checkout), SOURCE_ROOT, WORK, RESULT_PATH.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tarfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Result, Stop, git_url, installed_commit, make_venv, pip_install, pip_spec, run, tail  # noqa: E402

GATES_ROOT = Path(__file__).resolve().parent.parent


class Side:
    """One version under test: its interpreter, and — in checkout mode — the directory the
    scenario runs in and the environment it runs with. In pip mode both are None: the
    scenario imports the installed distribution, exactly as before v2."""

    def __init__(self, interp: Path, cwd: Path = None, env: dict = None):
        self.interp, self.cwd, self.env = interp, cwd, env

    def run(self, argv: list[str], extra_env: dict = None) -> tuple[int, str]:
        env = None
        if self.env is not None or extra_env:
            env = dict(self.env if self.env is not None else os.environ)
            env.update(extra_env or {})
        return run([str(self.interp), *argv], cwd=self.cwd, env=env)


def checkout(repository: str, ref: str, dest: Path) -> tuple[int, str, str]:
    """A shallow checkout of `ref` (a tag or a full sha) → (code, HEAD sha or "", output)."""
    dest.mkdir(parents=True, exist_ok=True)
    out_all = ""
    for argv in (["git", "init", "-q"], ["git", "fetch", "-q", "--depth", "1", git_url(repository), ref],
                 ["git", "checkout", "-q", "FETCH_HEAD"]):
        code, out = run(argv, cwd=dest)
        out_all += out
        if code != 0:
            return code, "", out_all
    code, out = run(["git", "rev-parse", "HEAD"], cwd=dest)
    return code, (out.strip().splitlines() or [""])[-1] if code == 0 else "", out_all + out


def checkout_side(repository: str, ref: str, work: Path, name: str, python: str,
                  source_root: str) -> tuple[Side, str, str]:
    """Check out `ref`, install its requirements.txt into a fresh virtualenv → (side, HEAD, error)."""
    src = work / f"src-{name}"
    code, head, out = checkout(repository, ref, src)
    if code != 0 or not head:
        return None, "", f"checkout of {ref} failed: {tail(out)}"
    interp = make_venv(work / f"venv-{name}", f"python{python}")
    req = src / "requirements.txt"
    if req.is_file():
        code, out = pip_install(interp, ["-r", str(req)], cwd=src)
        if code != 0:
            return None, head, f"requirements.txt of {ref} did not install: {tail(out)}"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(src / source_root), env.get("PYTHONPATH", "")) if p)
    env.update({"APP_SOURCE": str(src), "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy"})
    return Side(interp, cwd=src, env=env), head, ""


def digests(root: Path) -> dict[str, str]:
    """Every file under root → sha256; directories as 'dir' so an emptied slot is seen too."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = "dir" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def diff_files(before: dict, after: dict) -> list[str]:
    lines = []
    for k in sorted(set(before) | set(after)):
        if k not in after:
            lines.append(f"removed {k}")
        elif k not in before:
            lines.append(f"added {k}")
        elif before[k] != after[k]:
            lines.append(f"changed {k}")
    return lines


def view(side: Side, scenario: Path, saves: Path, mode: str = "read", env: dict = None) -> tuple[int, object, str]:
    code, out = side.run([str(scenario), mode, str(saves)], extra_env=env)
    if code != 0:
        return code, None, out
    try:
        return 0, json.loads(out.strip().splitlines()[-1]), out
    except (json.JSONDecodeError, IndexError):
        return 1, None, out


def first_difference(a: object, b: object, path: str = "") -> str:
    """The path of the first place two views differ, descending into dicts and equal-length lists."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if a.get(k) != b.get(k):
                return first_difference(a.get(k), b.get(k), f"{path}.{k}" if path else str(k))
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                name = x.get("name") if isinstance(x, dict) else None
                return first_difference(x, y, f"{path}[{i}{' ' + repr(name) if name else ''}]")
    return f"{path}: baseline {json.dumps(a)[:300]} vs candidate {json.dumps(b)[:300]}"


def describe_view_diff(ref: dict, got: dict) -> str:
    keys = sorted(set(ref) | set(got)) if isinstance(ref, dict) and isinstance(got, dict) else []
    bad = [k for k in keys if ref.get(k) != got.get(k)]
    if not bad:
        return "views differ"
    k = bad[0]
    if isinstance(ref.get(k), (dict, list)):
        return f"{len(bad)} field(s) differ; first at {first_difference(ref.get(k), got.get(k), k)}"
    return (f"{len(bad)} field(s) differ, first `{k}`: baseline {json.dumps(ref.get(k))[:600]} "
            f"vs candidate {json.dumps(got.get(k))[:600]}")


def strip_fidelity(v: object) -> object:
    if isinstance(v, dict):
        return {k: strip_fidelity(x) for k, x in v.items() if k != "fidelity"}
    if isinstance(v, list):
        return [strip_fidelity(x) for x in v]
    return v


def fidelity_failures(v: object, path: str = "") -> list[str]:
    """Paths of every fidelity value that is not exactly true."""
    out = []
    if isinstance(v, dict):
        for k, x in v.items():
            if k == "fidelity" and isinstance(x, dict):
                out += [f"{path}.{name}" if path else name for name, ok in x.items() if ok is not True]
            elif k == "fidelity":
                out.append(f"{path}.fidelity (not an object)")
            else:
                out += fidelity_failures(x, f"{path}.{k}" if path else str(k))
    elif isinstance(v, list):
        for i, x in enumerate(v):
            out += fidelity_failures(x, f"{path}[{i}]")
    return out


def compare(ref: object, got: object) -> tuple[bool, str]:
    """The candidate's view against the baseline's: equal outside the fidelity blocks, and
    every fidelity value in the candidate's view true."""
    a, b = strip_fidelity(ref), strip_fidelity(got)
    if a != b:
        return False, describe_view_diff(a, b)
    bad = fidelity_failures(got)
    if bad:
        return False, f"the candidate does not reproduce what the save files record at {len(bad)} place(s): {bad[:5]}"
    base_bad = fidelity_failures(ref)
    return True, ("candidate view equals the baseline view" + (
        f"; the baseline did not reproduce its own files at {len(base_bad)} place(s) and the candidate does: {base_bad[:5]}"
        if base_bad else ""))


def counts(v: object) -> dict:
    """The countable facts of a view, what a fixture manifest's `expected` may promise."""
    saves = (v or {}).get("saves") or [] if isinstance(v, dict) else []
    return {"saves": len(saves),
            "unreadable": sum(1 for s in saves if (s.get("metadata") or {}).get("unreadable")),
            "valid": sum(1 for s in saves if s.get("validation") == "valid"),
            "next_slot": (v or {}).get("next_slot") if isinstance(v, dict) else None}


def fixture_phase(result: Result, base: Side, cand: Side, scenario: Path, work: Path,
                  url: str, manifest_url: str) -> None:
    fx = work / "fixture"
    fx.mkdir(parents=True, exist_ok=True)
    manifest = {}
    try:
        archive = work / "fixture.tar.gz"
        urllib.request.urlretrieve(url, archive)
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                if m.name.startswith(("/", "..")) or "/../" in m.name or not (m.isfile() or m.isdir()):
                    raise ValueError(f"unsafe member {m.name!r}")
            t.extractall(fx)
        if manifest_url:
            with urllib.request.urlopen(manifest_url, timeout=60) as r:
                manifest = json.loads(r.read().decode("utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest is not a JSON object")
    except Exception as e:  # noqa: BLE001 — a fixture that was asked for and cannot be read blocks
        result.check("fixture", False, f"{url}: {type(e).__name__}: {e}")
    before = digests(fx)
    result.doc["fixture"] = {"url": url, "manifestUrl": manifest_url or None, "manifest": manifest or None,
                             "files": len([v for v in before.values() if v != "dir"])}
    result.check("fixture", bool(before), f"{result.doc['fixture']['files']} file(s) unpacked"
                 + (f"; manifest {manifest.get('slug')}/{manifest.get('version')}" if manifest else ""))
    env = {}
    if manifest.get("primaryFile"):
        env["TAK_PRIMARY_FILE"] = str(manifest["primaryFile"])
    code, ref, out = view(base, scenario, fx, env=env)
    result.check("fixture-baseline", code == 0 and ref is not None and digests(fx) == before,
                 f"reference view: {json.dumps(counts(ref))}" if code == 0 else tail(out))
    expected = manifest.get("expected") or {}
    if expected:
        got = counts(ref)
        bad = {k: (v, got.get(k)) for k, v in expected.items() if got.get(k) != v}
        result.check("fixture-expected", not bad,
                     f"manifest promises {json.dumps(expected)}; the baseline reports {json.dumps(got)}")
    for i in (1, 2):
        code, got_view, out = view(cand, scenario, fx, env=env)
        same, why = compare(ref, got_view) if code == 0 and got_view is not None else (False, tail(out))
        result.check(f"fixture-read-{i}", same, why)
        changed = diff_files(before, digests(fx))
        result.check(f"fixture-files-kept-{i}", not changed,
                     "every fixture file byte-identical" if not changed else "; ".join(changed[:20]))


def main() -> int:
    repository, sha = os.environ["REPOSITORY"], os.environ["SHA"]
    baseline_ref = os.environ.get("BASELINE_REF", "").strip()
    distribution = os.environ.get("DISTRIBUTION", "").strip() or repository.rsplit("/", 1)[-1]
    scenario = (GATES_ROOT / os.environ["SCENARIO"]).resolve()
    python = os.environ.get("PYTHON_VERSION", "").strip() or "3.12"
    work = Path(os.environ.get("WORK", "work")).resolve()
    result = Result("save-compat", repository, sha, os.environ.get("RESULT_PATH", str(work / "result.json")))
    mode = os.environ.get("INSTALL_MODE", "").strip() or "pip"
    source_root = os.environ.get("SOURCE_ROOT", "").strip() or "src"
    result.doc.update({"baselineRef": baseline_ref, "scenario": os.environ["SCENARIO"], "pythonVersion": python,
                       "installMode": mode})
    saves = work / "saves"
    try:
        if not baseline_ref:
            result.check("baseline-install", False, "no baseline ref given")
        if not scenario.is_file() or GATES_ROOT not in scenario.parents:
            result.check("harness", False, f"scenario {os.environ['SCENARIO']} is not a file in the gates repository")

        if mode not in ("pip", "checkout"):
            result.check("harness", False, f"unknown install_mode {mode!r} (pip | checkout)")
        if mode == "checkout":
            base, base_commit, err = checkout_side(repository, baseline_ref, work, "baseline", python, source_root)
            result.doc["baselineSha"] = base_commit or None
            result.check("baseline-install", base is not None,
                         f"{baseline_ref} checked out at {base_commit}; requirements installed" if base else err)
            cand, got, err = checkout_side(repository, sha, work, "candidate", python, source_root)
            result.check("candidate-install", cand is not None and got == sha,
                         f"checked out at {got}; requirements installed" if cand and got == sha
                         else (err or f"HEAD is {got}, not {sha}"))
        else:
            base = Side(make_venv(work / "venv-baseline", f"python{python}"))
            code, out = pip_install(base.interp, [pip_spec(distribution, repository, baseline_ref)])
            base_commit = installed_commit(base.interp, distribution) if code == 0 else None
            result.doc["baselineSha"] = base_commit
            result.check("baseline-install", code == 0 and bool(base_commit),
                         f"{distribution} {baseline_ref} installed at {base_commit}" if code == 0 else f"install failed: {tail(out)}")

            cand = Side(make_venv(work / "venv-candidate", f"python{python}"))
            code, out = pip_install(cand.interp, [pip_spec(distribution, repository, sha)])
            got = installed_commit(cand.interp, distribution) if code == 0 else None
            result.check("candidate-install", code == 0 and got == sha,
                         f"{distribution} installed at {got}" if code == 0 else f"install failed: {tail(out)}")

        saves.mkdir(parents=True, exist_ok=True)
        code, out = base.run([str(scenario), "write", str(saves)])
        written = digests(saves)
        result.doc["files"] = len([v for v in written.values() if v != "dir"])
        result.check("baseline-write", code == 0 and bool(written),
                     f"{result.doc['files']} file(s) written by {baseline_ref}" if code == 0 else tail(out))
        code, ref, out = view(base, scenario, saves)
        result.check("baseline-read", code == 0 and ref is not None and digests(saves) == written,
                     f"reference view: {json.dumps(ref)[:400]}" if code == 0 else tail(out))
        result.doc["baselineView"] = ref

        for i in (1, 2):
            code, got_view, out = view(cand, scenario, saves)
            same, why = compare(ref, got_view) if code == 0 and got_view is not None else (False, tail(out))
            result.check(f"read-{i}", same, why)
            changed = diff_files(written, digests(saves))
            result.check(f"files-kept-{i}", not changed,
                         f"{result.doc['files']} file(s) byte-identical" if not changed else "; ".join(changed[:20]))

        code, out = cand.run([str(scenario), "append", str(saves)])
        result.check("append", code == 0, "candidate added a save beside the baseline's" if code == 0 else tail(out))
        after = digests(saves)
        changed = [d for d in diff_files(written, after) if not d.startswith("added ")]
        result.check("files-kept-3", not changed,
                     f"every baseline file byte-identical; added {len(set(after) - set(written))} path(s)"
                     if not changed else "; ".join(changed[:20]))
        code, final, out = view(cand, scenario, saves)
        ok = code == 0 and isinstance(final, dict) and isinstance(ref, dict)
        missing = []
        unfaithful = []
        if ok:
            old = {json.dumps(strip_fidelity(s), sort_keys=True) for s in ref.get("saves") or []}
            now = {json.dumps(strip_fidelity(s), sort_keys=True) for s in final.get("saves") or []}
            missing = sorted(old - now)
            unfaithful = fidelity_failures(final)
            ok = not missing and not unfaithful and len(now) == len(old) + 1
        result.doc["candidateView"] = final
        result.check("read-3", ok,
                     f"{len((final or {}).get('saves') or [])} save(s) listed: every baseline save unchanged + 1 added" if ok
                     else (f"baseline save(s) no longer reported as before: {missing[:3]}" if missing
                           else f"the candidate does not reproduce what the save files record: {unfaithful[:5]}" if unfaithful
                           else (tail(out) if code != 0 else f"unexpected listing: {json.dumps(final)[:600]}")))
        fixture_url = os.environ.get("FIXTURE_URL", "").strip()
        if fixture_url:
            fixture_phase(result, base, cand, scenario, work, fixture_url,
                          os.environ.get("FIXTURE_MANIFEST_URL", "").strip())
        elif os.environ.get("FIXTURE_MANIFEST_URL", "").strip():
            result.check("fixture", False, "a fixture manifest was given without the fixture it describes")
    except Stop:
        pass
    except Exception as e:  # noqa: BLE001
        return result.crash(e)
    return result.finish()


if __name__ == "__main__":
    sys.exit(main())
