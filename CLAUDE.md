# CLAUDE.md

## Project overview

python-release-gates holds reusable GitHub Actions workflows that check whether a commit of a Python library (or, from `v2`, an application run from a checkout) is fit to become a stable release. Each gate runs one kind of check against a candidate commit, stops at the first failing assertion, and records the evidence as a `result.json` artifact. The gates decide nothing and publish nothing; release policy lives elsewhere. `README.md` documents every input and assertion.

## Technology stack

- Python 3 standard library only (no third-party dependencies in this repo; no `requirements.txt` or `pyproject.toml`). CI runs Python 3.12.
- GitHub Actions: `workflow_dispatch` + `workflow_call` reusable workflows, read-only `contents: read` token, no secrets.
- Candidates and consumers are installed into throwaway virtualenvs created by the harness (`venv` + `pip`).

## Build and run commands

There is no build. The CI check (`.github/workflows/check.yml`, job `compile`, on push to `main` and on PRs) runs:

```bash
python -m py_compile gates/*.py scenarios/*.py
python -m unittest discover -s tests   # tests/: the gate helpers (declared migrations)
```

Each gate is a plain script driven by environment variables, so it can be run locally the same way the workflow runs it. Example (save-compatibility gate for tak, pip mode):

```bash
REPOSITORY=Stephenson-Software/tak SHA=<full candidate sha> \
BASELINE_REF=v0.2.0 SCENARIO=scenarios/tak_slots.py DISTRIBUTION=tak \
WORK=/tmp/prg-work python3.12 gates/save_compat.py
```

The other gates follow the same pattern:

| Script | Required env | Optional env |
|---|---|---|
| `gates/tests_gate.py` | `REPOSITORY`, `SHA` (expects the candidate checked out at `$WORK/src`, as the workflow does) | `PACKAGE`, `PYTHON_VERSIONS`, `INSTALL_EXTRAS`, `TEST_COMMAND`, `EXPECTED_VERSION`, `WORK`, `RESULT_PATH` |
| `gates/consumers_gate.py` | `REPOSITORY`, `SHA`, `CONSUMERS` | `DISTRIBUTION`, `PYTHON_VERSION`, `TEST_COMMAND`, `PIN_SOURCES`, `WORK`, `RESULT_PATH` |
| `gates/save_compat.py` | `REPOSITORY`, `SHA`, `BASELINE_REF`, `SCENARIO` | `DISTRIBUTION`, `PYTHON_VERSION`, `FIXTURE_URL`, `FIXTURE_MANIFEST_URL`, `INSTALL_MODE` (`pip`/`checkout`), `SOURCE_ROOT`, `WORK`, `RESULT_PATH` |

`WORK` defaults to `./work` (gitignored); `RESULT_PATH` defaults to `$WORK/result.json`. Exit code is 0 only when every assertion passed. Render a result as Markdown with:

```bash
python gates/summarize.py work/result.json "Save-compatibility gate"
```

Dispatching a gate on GitHub (see `README.md` for more examples):

```bash
gh workflow run save-compat.yml --repo Stephenson-Software/python-release-gates --ref v2 \
  -f repository=Stephenson-Software/tak -f sha=<commit> \
  -f baseline_ref=v0.2.0 -f scenario=scenarios/tak_slots.py -f distribution=tak
```

## Test commands

`tests/` holds unit tests for the gate helpers. Verification is:

1. `python -m py_compile gates/*.py scenarios/*.py` and `python -m unittest discover -s tests` (what CI runs).
2. A real local run of every gate a change touches, against a real candidate (as in the example above), with the `PASS`/`FAIL` assertion lines reported.

## Repository layout

```
gates/common.py           Shared harness: Result (result.json, stop-at-first-failure), run(), pip_install() with one retry, venv helpers
gates/tests_gate.py       Tests gate: the library's own suite at the candidate, per interpreter, installed as a consumer would
gates/consumers_gate.py   Consumers gate: each pinning project's suite on its current pin (control) and on the candidate
gates/save_compat.py      Save-compatibility gate: baseline writes saves, candidate reads/appends; fixtures; pip or checkout mode
gates/summarize.py        result.json -> Markdown for $GITHUB_STEP_SUMMARY
scenarios/                Per-library save scenarios: `write DIR`, `read DIR` (one JSON line), `append DIR`
  tak_slots.py            tak's save-slot layer (tak.saves)
  roam_saves.py           roam worlds (checkout mode)
  trace_client_install_id.py  trace-client-python install-ID file (install_id_from_file)
  gardener_state.py       gardener's per-device state dir (run history, lists, cursor, hub store)
  usd_recorder_recordings.py  usd-recorder's .usda/.usdc recordings and usd-replay/1 replays
  menu_bot.py             a seeded bot standing in for a menu-driven game's front-end (used by the game scenarios)
  tak_game_saves.py       Tidewater / Overwinter / Night Ferry saves, played through the game (checkout mode)
  fishe_saves.py          FishE saves, flat (2.x) and slotted layouts, played through the game (checkout mode)
  cave_saves.py           Cave's savefile.txt, played on stdin (checkout mode, source_root .)
  ophidian_saves.py       ophidian's save.json, played through its text-UI loop (checkout mode)
.github/workflows/        tests.yml, consumers.yml, save-compat.yml (the gates) + check.yml (CI for this repo)
```

## Coding conventions

- Standard library only; `from __future__ import annotations` and type hints in harness code; 4-space indent; `snake_case` functions.
- Every gate builds a `Result`, calls `result.check(name, ok, detail)` per assertion (which raises `Stop` on the first failure), and always writes `result.json`, including on a harness crash (`result.crash(e)` records an assertion named `harness`).
- An assertion named `harness` or `control` means the check could not run; it must never read as a verdict on the candidate.
- Each module starts with a docstring that lists its assertions in order; keep it, the workflow header comment and the README tables in sync with the code.
- Workflows declare the same inputs under both `workflow_dispatch` and `workflow_call`, pass them to the script as env vars (never interpolated into `run:` shell text), and name the job `<repository> @ <sha>`.
- A new scenario implements the three modes `write DIR`, `read DIR`, `append DIR` and drives only the library's public save API.

## Do / Don't

**Do:**
- Keep each gate's token read-only (`permissions: contents: read`) and secret-free.
- Treat any doubt about save data as a failure: a requested fixture that cannot be downloaded or unpacked fails the gate, never skips it.
- Add a new gate behaviour behind a new input with a default that preserves current behaviour, and document it in the README input table.
- Retag (`v1`, `v2`, ...) only when asked; callers reference tags.

**Don't:**
- Don't move or rewrite existing tags (`v1`, `v2`, `tak/...`, `roam/...`); a change here must not alter a check already in progress, which is why callers pin a tag and never a branch.
- Don't add publishing, secrets, or write permissions to any workflow.
- Don't let a gate continue after a failing assertion, or make any check waivable.
- Don't add third-party dependencies to the harness.
- Don't commit `work/` or `__pycache__/`.
