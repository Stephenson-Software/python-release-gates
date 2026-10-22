"""Save-compatibility scenario for gardener (https://github.com/Stephenson-Software/gardener).

gardener keeps its state in one directory per device (`GARDENER_STATE_DIR`): a SQLite run
history (`state.py`, the `runs` table with its additive migrations), the garden list, the
merge allow-list, the overnight resume cursor, the operator's `notify.env` and `hub.env`,
and, on a device that serves a hub, the hub's own store under `hub/` (the same `runs`
table fed by every device, plus `device_lists` and `device_live`, in WAL mode). The owner
runs gardener on several devices that share that state, so a store an older release wrote
has to read the same through a newer one. This scenario writes every one of those through
gardener's own functions, never hand-written SQL or JSON (the two `.env` files are the
exception: gardener only reads them, the operator writes them):

  desk          runs of every outcome gardener records (`tend`, `report`, `implement`,
                `file-issue`, `created`, `created_incomplete`, `error`), timestamps in
                every shape the readers tolerate (aware, `Z`, naive, unparseable), costs
                and durations present and absent, a run whose uuid and device were given
                by the caller; some runs acknowledged by a hub (`mark_pushed`), the rest
                still in the outbox; garden and allow-list edits, both overnight cursor
                keys, notify.env and hub.env; and a hub store holding both devices' pushed
                runs (one push sent twice), both devices' lists and a heartbeat
  phone         a second device: its own runs two days later, its own lists, no hub

Modes: `write DIR` (baseline), `read DIR` (prints one JSON line), `append DIR`.

`read` reports, per device directory, what the version's read functions return: the
device name, hub settings, both lists, the cursor, every run (all `Run` fields), the
per-repo and per-day aggregates, the latest session, the outbox, and the hub store's runs,
device lists, device summaries, live states and digest. Every clock the readers take is
pinned to a fixed `now`. Aggregates are reported field by field for the fields the
baseline already had, so a field a newer release adds is not mistaken for a changed save.

`append` cannot write into the baseline's store: any write changes the bytes of a SQLite
file, and the gate holds every baseline file byte-identical. It copies the `desk`
directory to `desk.upgraded` (what an upgraded device holds the moment it first runs) and
records one run there through the candidate's writer path, which runs the candidate's
migrations on the baseline's file, then opens the copied hub store the way the hub's
server does on start (`prepare_store`), pushes the run into it and marks it pushed. Before
it exits it checks that every run, aggregate and list the copy held before the write still
reads exactly the same, and fails otherwise.

Isolation: HOME is a throwaway directory, every gardener environment variable that could
point elsewhere (device name, webhook, hub URL and token, usage reporting) is cleared, and
nothing here dispatches, pushes over the network, posts to Discord or calls gh.
"""

import dataclasses
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

for name in [k for k in os.environ if k.startswith(("GARDENER_", "DISCORD_", "TRACE_"))]:
    del os.environ[name]
os.environ["HOME"] = tempfile.mkdtemp(prefix="gardener-home-")
os.environ["DO_NOT_TRACK"] = "1"
os.environ["GARDENER_USAGE_REPORTING_ENABLED"] = "0"

from gardener import garden, hub, merge_allowlist, notify, overnight, state  # noqa: E402

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 9, 2, 0, 0, 0, tzinfo=timezone.utc)
UPGRADED = "desk.upgraded"
PROBE_REPOS = ["Org/alpha", "Org/beta", "Org/gamma", "Org/delta", "Org/never"]
MODES = ["tend", "report", "implement", "file-issue", "create-dev-loop"]

DESK_RUNS = [
    dict(repo="Org/alpha", mode="tend", outcome="tend", timestamp="2026-09-01T02:00:00+00:00",
         gap_summary="tended: 2 issues closed", exit_code=0, duration_ms=60000, cost_usd=1.25,
         claude_session_id="sess-a1"),
    dict(repo="Org/alpha", mode="tend", outcome="error", timestamp="2026-09-01T02:30:00+00:00",
         gap_summary="claude exited 1\nsession limit reached", exit_code=1, duration_ms=1500),
    dict(repo="Org/beta", mode="report", outcome="report", timestamp="2026-09-01T03:00:00+00:00",
         gap_summary="gaps: README — ünïcode ✓", exit_code=0, duration_ms=42000, cost_usd=0.5),
    dict(repo="Org/beta", mode="create-dev-loop", outcome="created", timestamp="2026-09-02T01:00:00Z",
         exit_code=0, cost_usd=2.0, run_uuid="00000000-0000-4000-8000-000000000001", device="desk-old-name"),
    dict(repo="Org/beta", mode="tend", outcome="created_incomplete", timestamp="2026-09-02T01:10:00+00:00",
         exit_code=0, duration_ms=300000, cost_usd=3.333333),
    dict(repo="Org/gamma", mode="implement", outcome="implement", timestamp="2026-09-02 01:20:00",
         exit_code=0, duration_ms=120000, cost_usd=0.75),
    dict(repo="Org/gamma", mode="file-issue", outcome="file-issue", timestamp="2026-09-02T01:30:00+00:00"),
    dict(repo="Org/gamma", mode="tend", outcome="error", timestamp="2026-09-02T01:40:00+00:00",
         gap_summary="clone dirty", exit_code=2),
    dict(repo="Org/delta", mode="tend", outcome="error", timestamp="not a date",
         gap_summary="hand-edited row"),
]
PHONE_RUNS = [
    dict(repo="Org/alpha", mode="tend", outcome="tend", timestamp="2026-09-03T22:00:00+00:00",
         exit_code=0, duration_ms=90000, cost_usd=0.9),
    dict(repo="Org/epsilon", mode="tend", outcome="error", timestamp="2026-09-03T22:05:00+00:00",
         gap_summary="no dev-loop skill", exit_code=1),
]
HEARTBEAT = {
    "seq": 7, "sent_at": "2026-09-02T01:39:00+00:00", "interval_seconds": 300, "ending": False,
    "gardener_version": "0.4.7",
    "session": {"id": "night-1", "command": "overnight", "target": "garden",
                "started_at": "2026-09-02T00:55:00+00:00"},
    "in_progress": ["Org/gamma"],
    "batch_progress": {"start": 2, "end": 4, "total": 5},
    "overnight_run": {"strategy": "random", "budget_hours": 6, "garden_size_at_start": 5,
                      "started_at": "2026-09-02T00:55:00+00:00", "elapsed_seconds": 2640,
                      "remaining_seconds": 18960},
    "slots": [{"repo": "Org/gamma", "phase": "running", "started_at": "2026-09-02T01:35:00+00:00",
               "idle_seconds": 12, "stalled": False, "rate_limit_seen": True}],
}
REPO_STATS_FIELDS = ("repo", "runs", "successes", "errors", "first_run", "last_run", "last_success",
                     "last_outcome", "cost_usd", "duration_ms")
SESSION_FIELDS = ("runs", "errors", "cost_usd", "duration_ms", "started_at", "ended_at")


def use(device_dir):
    """Point gardener's default paths at one device's state directory."""
    os.environ["GARDENER_STATE_DIR"] = str(device_dir)
    return Path(device_dir)


def hub_db(device_dir):
    return Path(device_dir) / "hub" / "gardener.sqlite3"


def record(runs):
    out = []
    for r in runs:
        run = state.Run(**r)
        state.record_run(run)
        out.append(run)
    return out


def write_env(path, values):
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))


def write_desk(root):
    d = use(root / "desk")
    d.mkdir(parents=True)
    write_env(d / "notify.env", {"GARDENER_DEVICE_NAME": "desk",
                                 "DISCORD_WEBHOOK_URL": "https://discord.invalid/api/webhooks/0/gate"})
    write_env(d / hub.CONFIG_FILENAME, {hub.URL_ENV: "https://hub.invalid/", hub.TOKEN_ENV: "gate-token",
                                        hub.HEARTBEAT_ENV: "300"})
    for repo in ("Org/gamma", "Org/alpha", "Org/beta", "Org/delta", "Org/alpha"):
        garden.add(repo)
    garden.remove("Org/delta")
    for repo in ("Org/beta", "Org/alpha"):
        merge_allowlist.add(repo)
    overnight.write_cursor(3)
    overnight.write_attempted(["Org/gamma", "Org/alpha"])
    runs = record(DESK_RUNS)
    pushed = runs[:4]
    state.mark_pushed([r.run_uuid for r in pushed], "2026-09-02T02:00:00+00:00")
    return pushed


def write_phone(root):
    d = use(root / "phone")
    d.mkdir(parents=True)
    write_env(d / "notify.env", {"GARDENER_DEVICE_NAME": "phone"})
    garden.add("Org/epsilon")
    garden.add("Org/alpha")
    merge_allowlist.add("Org/epsilon")
    merge_allowlist.remove("Org/epsilon")
    overnight.write_attempted(["Org/epsilon"])
    return record(PHONE_RUNS)


def write(root):
    root = Path(root)
    pushed = write_desk(root)
    phone_runs = write_phone(root)
    use(root / "desk")
    db = hub.prepare_store(root / "desk" / "hub")
    state.insert_runs(pushed + phone_runs, db_path=db)
    state.insert_runs(phone_runs, db_path=db)  # a push whose response was lost, sent again
    hub.store_device_lists(db, "desk", {"garden": ["Org/alpha", "Org/beta", "Org/gamma"],
                                        "merge_allowlist": ["Org/alpha", "Org/beta"]})
    hub.store_device_lists(db, "phone", {"garden": ["Org/alpha", "Org/epsilon"], "merge_allowlist": []})
    hub.store_heartbeat(db, "desk", hub.heartbeat_from_wire(HEARTBEAT), received_at="2026-09-02T01:39:05+00:00")


def run_dict(run):
    return dataclasses.asdict(run)


def pick(obj, fields):
    return {f: getattr(obj, f) for f in fields}


def runs_view(db=None):
    """Every reader of the `runs` table, over one store."""
    runs = state.list_runs(db_path=db, limit=100000)
    repos = sorted({r.repo for r in runs})
    session = state.session_stats(db_path=db)
    latest = {}
    for repo in repos:
        for mode in MODES:
            when = state.latest_success_at(repo, mode, db_path=db)
            if when is not None:
                latest[f"{repo} {mode}"] = when.isoformat()
    return {
        "runs": [run_dict(r) for r in runs],
        "by_repo": {repo: [r.run_uuid for r in state.list_runs(db_path=db, repo=repo, limit=100000)]
                    for repo in repos},
        "since": [r.run_uuid for r in state.runs_since(SINCE, db_path=db)],
        "latest_success": latest,
        "repo_stats": {k: pick(v, REPO_STATS_FIELDS) for k, v in sorted(state.repo_stats(db_path=db).items())},
        "session": {**pick(session, SESSION_FIELDS), "errors_detail": [run_dict(r) for r in session.errors_detail]},
        "daily": [dataclasses.asdict(d) for d in state.daily_stats(db_path=db, days=30)],
        "count": state.count_runs(db_path=db),
        "pending_push": list(state.pending_push_summary(db_path=db)),
    }


def hub_view(db):
    if not db.exists():
        return None
    return {
        **runs_view(db),
        "device_lists": hub.union_device_lists(db),
        "devices": hub.device_summaries(db, now=NOW),
        "phone_session": pick(state.session_stats(db_path=db, device="phone"), SESSION_FIELDS),
        "live": hub.live_device_states(db, now=NOW),
        "digest": hub.digest(db, hours=72, now=NOW),
    }


def device_view(device_dir):
    d = use(device_dir)
    config = hub.load_config()
    return {
        "name": d.name,
        "device_name": notify.load_device_name(),
        "webhook": notify.load_webhook_url(),
        "hub_config": dataclasses.asdict(config) if config else None,
        "garden": garden.list_garden(),
        "in_garden": [r for r in PROBE_REPOS if garden.is_in_garden(r)],
        "merge_allowlist": merge_allowlist.list_allowed(),
        "allowed": [r for r in PROBE_REPOS if merge_allowlist.is_allowed(r)],
        "overnight": {"next_index": overnight.read_cursor(), "attempted": overnight.read_attempted()},
        "store": runs_view(),
        "hub_store": hub_view(hub_db(d)),
    }


def read(root):
    root = Path(root)
    saves = [device_view(p) for p in sorted(root.iterdir()) if p.is_dir()]
    print(json.dumps({"saves": saves}, sort_keys=True))


def append(root):
    root = Path(root)
    dest = root / UPGRADED
    if dest.exists():
        raise SystemExit(f"{dest} already exists")
    shutil.copytree(root / "desk", dest)
    before = device_view(dest)
    use(dest)
    run = state.Run(repo="Org/alpha", mode="tend", outcome="tend", timestamp="2026-09-03T01:00:00+00:00",
                    gap_summary="first run after the upgrade", exit_code=0, duration_ms=45000, cost_usd=0.4)
    state.record_run(run)
    db = hub.prepare_store(dest / "hub")
    held = state.insert_runs([run], db_path=db)
    state.mark_pushed(held, "2026-09-03T01:05:00+00:00")
    after = device_view(dest)
    for store in ("store", "hub_store"):
        old = before[store]["runs"]
        kept = [r for r in after[store]["runs"] if r["run_uuid"] != run.run_uuid]
        if kept != old or len(after[store]["runs"]) != len(old) + 1:
            raise SystemExit(f"{store}: the runs the baseline recorded no longer read the same after one more was recorded")
    for key in ("device_name", "webhook", "hub_config", "garden", "merge_allowlist", "overnight"):
        if before[key] != after[key]:
            raise SystemExit(f"{key} changed when a run was recorded: {before[key]!r} -> {after[key]!r}")
    if before["hub_store"]["device_lists"] != after["hub_store"]["device_lists"]:
        raise SystemExit("the hub store's device lists changed when a run was pushed")
    if after["store"]["pending_push"] != before["store"]["pending_push"]:
        raise SystemExit(f"outbox changed: {before['store']['pending_push']} -> {after['store']['pending_push']}")


if __name__ == "__main__":
    mode, root = sys.argv[1], sys.argv[2]
    {"write": write, "read": read, "append": append}[mode](root)
