# python-release-gates

Reusable GitHub Actions workflows that check whether a commit of a Python library is fit to
become a stable release. Each gate runs one kind of check against the candidate commit and
stops at the first assertion that fails. The run log and a `result.json` artifact are the
evidence. The policy that decides what to do with that evidence lives elsewhere.

These workflows publish nothing and hold no secrets. Their token is read-only.

The first library covered is [tak](https://github.com/Stephenson-Software/tak), the
text-adventure kit that FishE, Tidewater and Overwinter are built on.

## Gates

| Workflow | Checks | Artifact |
|---|---|---|
| [`tests.yml`](.github/workflows/tests.yml) | The library's own test suite passes at the candidate commit on every supported interpreter, with the package installed as a consumer installs it, and the declared version is the one expected | `tests-<run id>` |
| [`consumers.yml`](.github/workflows/consumers.yml) | Every project that pins the library still passes its own test suite once the candidate replaces its current pin. The consumer's current pin is run first as a control, so a consumer that is already failing is reported as pre-existing rather than blamed on the candidate | `consumers-<run id>` |
| [`save-compat.yml`](.github/workflows/save-compat.yml) | Saves written by the baseline release are read by the candidate and produce the same view twice. Every file stays byte-identical, and a new save can be added beside the old ones without touching them | `save-compat-<run id>` |

Each workflow can be called two ways with the same inputs: `workflow_dispatch` (dispatched by
hand or by automation) and `workflow_call` (reused from a library's own workflow). The job is
named `<repository> @ <sha>` so a dispatched run can be found again.

## result.json

```json
{"gate": "tests", "repository": "owner/repo", "sha": "<candidate>", "candidateSha": "<candidate>",
 "passed": true, "assertions": [{"name": "tests-3.12", "passed": true, "detail": "259 passed in 4.1s"}]}
```

`candidateSha` is the commit the gate verified; for the consumers and save-compatibility
gates it is also the commit pip recorded for the installed copy (PEP 610). An assertion named
`harness` or `control` means the check could not be run at all. It is never a verdict on the
candidate.

## Tests gate

| Input | Default | Meaning |
|---|---|---|
| `repository`, `sha` | — | the candidate |
| `package` | `""` | import name; when set, the package must import from `site-packages` and its `__version__` (if any) must equal `expected_version` |
| `python_versions` | `3.12` | comma-separated interpreters |
| `install_extras` | `dev` | extras installed with the package |
| `test_command` | `python -m pytest -q` | run in the checkout; a leading `python` is the gate's virtualenv |
| `expected_version` | `""` | the `[project] version` pyproject.toml must declare |

Assertions: `checkout`, `version`, then for each interpreter `install-<py>`, `import-<py>`,
`tests-<py>`.

## Consumers gate

| Input | Default | Meaning |
|---|---|---|
| `repository`, `sha` | — | the candidate |
| `consumers` | — | one per line (or comma-separated): `owner/repo [extra pip package …]`, e.g. `Stephenson-Software/FishE pygame` |
| `distribution` | repository name | the name the consumers' `requirements.txt` uses |
| `python_version` | `3.12` | interpreter for every consumer |
| `test_command` | `python -m pytest -q` | run in each consumer's checkout |

For each consumer, its default branch is cloned and its own `requirements.txt` is installed.
Its suite is then run twice: once on the pin it carries today (`control-<Name>`) and once
with the candidate installed over it (`install-<Name>`, `candidate-<Name>`). A consumer whose
requirements do not name the library is skipped (`pin-<Name>` says so).

## Save-compatibility gate

| Input | Default | Meaning |
|---|---|---|
| `repository`, `sha` | — | the candidate |
| `baseline_ref` | — | the release the saves are written with (a tag) |
| `scenario` | — | a script in [`scenarios/`](scenarios) that drives the library's save API: `write DIR`, `read DIR` (one JSON line), `append DIR` |
| `distribution` | repository name | pip distribution name |
| `python_version` | `3.12` | interpreter for both versions |

Assertions: `baseline-install`, `candidate-install`, `baseline-write`, `baseline-read`,
`read-1`, `files-kept-1`, `read-2`, `files-kept-2`, `append`, `files-kept-3`, `read-3`.
A failure in any of them means saves may not survive the upgrade.

[`scenarios/tak_slots.py`](scenarios/tak_slots.py) covers tak's save **slot** layer:
numbered slot directories, the listing and metadata the save menu shows, damaged and empty
primary files (listed, unpickable, never reused), directories that are not slots, the next
free slot, and JSON Schema validation. Each game owns the format of the files inside a slot,
so that format is covered by the games' own test suites, which the consumers gate runs.

## Dispatch by hand

```
gh workflow run save-compat.yml --repo Stephenson-Software/python-release-gates --ref v1 \
  -f repository=Stephenson-Software/tak -f sha=<commit> \
  -f baseline_ref=v0.2.0 -f scenario=scenarios/tak_slots.py -f distribution=tak
```

Reference a tag, never a branch, so that a change here cannot alter a check already in
progress.

## License

MIT — see [LICENSE](LICENSE).

---

_drafted by Claude on behalf of Daniel Stephenson_
