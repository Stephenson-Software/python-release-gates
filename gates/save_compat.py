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

Any doubt about data fails the gate. None of these checks can be waived.

Environment: REPOSITORY, SHA, BASELINE_REF, DISTRIBUTION, SCENARIO, PYTHON_VERSION,
FIXTURE_URL, FIXTURE_MANIFEST_URL, WORK, RESULT_PATH.
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
from common import Result, Stop, installed_commit, make_venv, pip_install, pip_spec, run, tail  # noqa: E402

GATES_ROOT = Path(__file__).resolve().parent.parent


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


def view(interp: Path, scenario: Path, saves: Path, mode: str = "read", env: dict = None) -> tuple[int, object, str]:
    code, out = run([str(interp), str(scenario), mode, str(saves)], env=env)
    if code != 0:
        return code, None, out
    try:
        return 0, json.loads(out.strip().splitlines()[-1]), out
    except (json.JSONDecodeError, IndexError):
        return 1, None, out


def describe_view_diff(ref: dict, got: dict) -> str:
    keys = sorted(set(ref) | set(got)) if isinstance(ref, dict) and isinstance(got, dict) else []
    bad = [k for k in keys if ref.get(k) != got.get(k)]
    if not bad:
        return "views differ"
    k = bad[0]
    return (f"{len(bad)} field(s) differ, first `{k}`: baseline {json.dumps(ref.get(k))[:600]} "
            f"vs candidate {json.dumps(got.get(k))[:600]}")


def counts(v: object) -> dict:
    """The countable facts of a view, what a fixture manifest's `expected` may promise."""
    saves = (v or {}).get("saves") or [] if isinstance(v, dict) else []
    return {"saves": len(saves),
            "unreadable": sum(1 for s in saves if (s.get("metadata") or {}).get("unreadable")),
            "valid": sum(1 for s in saves if s.get("validation") == "valid"),
            "next_slot": (v or {}).get("next_slot") if isinstance(v, dict) else None}


def fixture_phase(result: Result, base: Path, cand: Path, scenario: Path, work: Path,
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
    env = dict(os.environ)
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
        result.check(f"fixture-read-{i}", code == 0 and got_view == ref,
                     "candidate view equals the baseline view" if code == 0 and got_view == ref
                     else (describe_view_diff(ref, got_view) if code == 0 and got_view is not None else tail(out)))
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
    result.doc.update({"baselineRef": baseline_ref, "scenario": os.environ["SCENARIO"], "pythonVersion": python})
    saves = work / "saves"
    try:
        if not baseline_ref:
            result.check("baseline-install", False, "no baseline ref given")
        if not scenario.is_file() or GATES_ROOT not in scenario.parents:
            result.check("harness", False, f"scenario {os.environ['SCENARIO']} is not a file in the gates repository")

        base = make_venv(work / "venv-baseline", f"python{python}")
        code, out = pip_install(base, [pip_spec(distribution, repository, baseline_ref)])
        base_commit = installed_commit(base, distribution) if code == 0 else None
        result.doc["baselineSha"] = base_commit
        result.check("baseline-install", code == 0 and bool(base_commit),
                     f"{distribution} {baseline_ref} installed at {base_commit}" if code == 0 else f"install failed: {tail(out)}")

        cand = make_venv(work / "venv-candidate", f"python{python}")
        code, out = pip_install(cand, [pip_spec(distribution, repository, sha)])
        got = installed_commit(cand, distribution) if code == 0 else None
        result.check("candidate-install", code == 0 and got == sha,
                     f"{distribution} installed at {got}" if code == 0 else f"install failed: {tail(out)}")

        saves.mkdir(parents=True, exist_ok=True)
        code, out = run([str(base), str(scenario), "write", str(saves)])
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
            result.check(f"read-{i}", code == 0 and got_view == ref,
                         "candidate view equals the baseline view" if code == 0 and got_view == ref
                         else (describe_view_diff(ref, got_view) if code == 0 and got_view is not None else tail(out)))
            changed = diff_files(written, digests(saves))
            result.check(f"files-kept-{i}", not changed,
                         f"{result.doc['files']} file(s) byte-identical" if not changed else "; ".join(changed[:20]))

        code, out = run([str(cand), str(scenario), "append", str(saves)])
        result.check("append", code == 0, "candidate added a save beside the baseline's" if code == 0 else tail(out))
        after = digests(saves)
        changed = [d for d in diff_files(written, after) if not d.startswith("added ")]
        result.check("files-kept-3", not changed,
                     f"every baseline file byte-identical; added {len(set(after) - set(written))} path(s)"
                     if not changed else "; ".join(changed[:20]))
        code, final, out = view(cand, scenario, saves)
        ok = code == 0 and isinstance(final, dict) and isinstance(ref, dict)
        missing = []
        if ok:
            old = {json.dumps(s, sort_keys=True) for s in ref.get("saves") or []}
            now = {json.dumps(s, sort_keys=True) for s in final.get("saves") or []}
            missing = sorted(old - now)
            ok = not missing and len(now) == len(old) + 1
        result.doc["candidateView"] = final
        result.check("read-3", ok,
                     f"{len((final or {}).get('saves') or [])} save(s) listed: every baseline save unchanged + 1 added" if ok
                     else (f"baseline save(s) no longer reported as before: {missing[:3]}" if missing
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
