"""Save-compatibility scenario for darkroom (https://github.com/Preponderous-Software/darkroom).

darkroom keeps state next to each shot list (`media.json`): `.darkroom/state.json` (one entry
per shot: the capture and encode hashes it was made with, its outputs, the darkroom version)
and `.darkroom/takes/<shot>/` (each take's frames and `meta.json`, which a re-encode reads
back). Its README promises that upgrading does not by itself make a shot stale, and 0.7.0's
changelog that "nothing is reshot on upgrade". So a project an older release shot must, under
a newer one, still load, still name the same outputs, and come out of a `shoot` with nothing
reshot or rewritten.

Every project here is shot with the `command` backend and a stand-in capture program
(`fake_capture.py`, standard library only, written beside the shot list and named through
`{manifest_dir}`), so no browser, display or ffmpeg is needed. The baseline's `write DIR`
lays down two projects:

  projects/game/   a still (jpg, vars in the capture), a clip (GIF, poster and contact
                   sheet), a still shot twice under the tags `before` and `after`, and a prep
                   (`kind: prep`) whose output file a dependent still reads (`depends`)
  projects/docs/   a still whose outputs were committed and recorded by `shoot --adopt`
                   (no take), and a still shot normally

`read DIR` leaves DIR as it found it. For each project it loads the shot list with the reading
version (`darkroom.manifest.load`) and runs that version's `shoot` over it, with lock and run
registries in a temporary directory. darkroom's capture hash includes the project's absolute
path, so the shoot has to run in place: the project is copied aside first and put back from
that copy afterwards, byte for byte. A shot is `kept` when its state entry, its take and every
output it lists are byte-identical after that shoot, and `rewritten` otherwise; a project whose
shot list does not load is reported as `error`. `projects` says, per project, whether it
loaded, how many shots that shoot failed, which state keys it added and which files it added
or removed. Each entry in `saves` (one per shot, prep and tagged take) also carries what the
reader makes of the state file: the entry's recorded outputs and hashes, whether they equal
the reading version's own capture/encode hashes, and the outputs that version expects
(`expected_outputs`). Nothing in the view depends on the machine or the time.

`append DIR` (the candidate) shoots a third project, projects/appended/, beside the others.

Modes: `write DIR`, `read DIR` (prints one JSON line), `append DIR`.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

FAKE_CAPTURE = r'''"""Writes N solid PNG frames (standard library only): fake_capture.py OUT_DIR W H [N] [R,G,B]."""
import struct
import sys
import zlib
from pathlib import Path


def png(width, height, rgb):
    row = b"\x00" + bytes(rgb) * width
    raw = zlib.compress(row * height, 9)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


out, width, height = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
frames = int(sys.argv[4]) if len(sys.argv) > 4 else 1
rgb = [int(x) for x in (sys.argv[5] if len(sys.argv) > 5 else "40,120,200").split(",")]
for i in range(frames):
    shade = [min(255, c + 12 * i) for c in rgb]
    (out / ("frame-%03d.png" % i)).write_bytes(png(width, height, shade))
'''

PREP = ("import pathlib, sys; d = pathlib.Path(sys.argv[1], 'data'); d.mkdir(exist_ok=True); "
        "(d / 'stats.json').write_text('{\"value\": %s}' % sys.argv[2])")


def fake(*args):
    return ["{python}", "{manifest_dir}/fake_capture.py", "{out_dir}", "{width}", "{height}", *args]


GAME = {
    "out": "docs/media",
    "vars": {"color": "40,120,200", "value": "40"},
    "shots": [
        {"name": "title", "kind": "still", "backend": "command", "device": {"width": 64, "height": 36},
         "argv": fake("1", "{color}"), "caption": "The title screen.", "alt": "A blue title screen"},
        {"name": "walk", "kind": "clip", "backend": "command", "device": {"width": 48, "height": 32},
         "argv": fake("6"), "gif": {"fps": 6}, "poster": 0.5, "sheet": True, "caption": "Walking."},
        {"name": "versus", "kind": "still", "backend": "command", "device": {"width": 32, "height": 32},
         "argv": fake("1", "{color}"), "format": "png"},
        {"name": "rig", "kind": "prep", "argv": ["{python}", "-c", PREP, "{manifest_dir}", "{value}"],
         "outputs": ["data/stats.json"]},
        {"name": "chart", "kind": "still", "backend": "command", "depends": ["rig"],
         "device": {"width": 40, "height": 30}, "argv": fake("1", "10,200,10"), "format": "png"},
    ],
}

DOCS = {
    "out": "media",
    "vars": {},
    "shots": [
        {"name": "committed", "kind": "still", "backend": "command", "device": {"width": 32, "height": 24},
         "argv": fake("1", "200,40,40"), "format": "png"},
        {"name": "menu", "kind": "still", "backend": "command", "device": {"width": 32, "height": 24},
         "argv": fake("1", "90,90,90"), "format": "png"},
    ],
}

APPENDED = {
    "out": "media",
    "vars": {},
    "shots": [
        {"name": "fresh", "kind": "still", "backend": "command", "device": {"width": 24, "height": 24},
         "argv": fake("1", "1,2,3"), "format": "png"},
    ],
}


def quiet(*_):
    pass


def isolate(tmp):
    """Lock and run registries out of the real ones (other darkroom runs may be live)."""
    os.environ["DARKROOM_LOCK_DIR"] = str(Path(tmp) / "locks")
    os.environ["DARKROOM_RUN_DIR"] = str(Path(tmp) / "runs")


def lay_down(project, data):
    project.mkdir(parents=True, exist_ok=True)
    (project / "fake_capture.py").write_text(FAKE_CAPTURE)
    data = json.loads(json.dumps(data))
    data["vars"]["python"] = sys.executable
    (project / "media.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return project / "media.json"


def shoot_or_die(path, **kw):
    from darkroom.manifest import load
    from darkroom.shoot import shoot
    failed = shoot(load(path), log=quiet, **kw)
    if failed:
        raise SystemExit(f"{path}: {failed} shot(s) failed")


def write(root):
    root = Path(root)
    with tempfile.TemporaryDirectory() as tmp:
        isolate(tmp)
        game = lay_down(root / "projects" / "game", GAME)
        shoot_or_die(game)
        shoot_or_die(game, only=["versus"], tag="before")
        shoot_or_die(game, only=["versus"], tag="after")

        docs = lay_down(root / "projects" / "docs", DOCS)
        shoot_or_die(docs, only=["menu"])
        # `committed`: outputs made elsewhere (shot in a scratch copy) and committed, then
        # recorded with `shoot --adopt`, so this project has no take for it.
        scratch = Path(tmp) / "scratch"
        shutil.copytree(docs.parent, scratch)
        shoot_or_die(scratch / "media.json", only=["committed"])
        (docs.parent / "media").mkdir(exist_ok=True)
        shutil.copyfile(scratch / "media" / "committed.png", docs.parent / "media" / "committed.png")
        from darkroom.manifest import load
        from darkroom.shoot import adopt
        if adopt(load(docs), only=["committed"], log=quiet) != 1:
            raise SystemExit("adopt did not record the committed shot")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def tree(path):
    """{relative file: sha256} under path (empty when it does not exist)."""
    if not path.is_dir():
        return {}
    return {str(f.relative_to(path)): sha(f) for f in sorted(path.rglob("*")) if f.is_file()}


def state_of(project):
    try:
        data = json.loads((project / ".darkroom" / "state.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def describe(project):
    """The reading version's view of one project (see the module docstring)."""
    from darkroom.manifest import load
    from darkroom.shoot import expected_outputs, shoot
    name = project.name
    entries = []
    try:
        m = load(project / "media.json")
    except Exception as e:  # noqa: BLE001 — a shot list the reader cannot load is the finding
        return [{"project": name, "result": "error", "error": type(e).__name__}], {"loaded": False}
    state = state_of(project)
    with tempfile.TemporaryDirectory() as tmp:
        isolate(tmp)
        # darkroom's capture hash includes the project's absolute path (`manifest_dir`), so a
        # copy elsewhere would reshoot by design. The shoot runs in place, and the project is
        # then put back exactly as it was from a copy taken first.
        before = Path(tmp) / "before"
        shutil.copytree(project, before)
        try:
            failed = shoot(load(project / "media.json"), log=quiet)
            after = state_of(project)
            shots = {s.name: s for s in m.shots}
            for key in sorted(state):
                entry = state[key] if isinstance(state[key], dict) else {}
                shot_name, _, tag = key.partition(".")
                shot = shots.get(shot_name)
                outputs = [str(o) for o in entry.get("outputs") or []]
                take = Path(".darkroom") / "takes" / key
                kept = (after.get(key) == entry and tree(before / take) == tree(project / take)
                        and all(sha(before / o) is not None and sha(before / o) == sha(project / o) for o in outputs))
                view = {"project": name, "key": key, "result": "kept" if kept else "rewritten",
                        "kind": entry.get("kind", "shot"), "outputs": outputs,
                        "recorded": {k: entry.get(k) for k in
                                     ("capture_hash", "encode_hash", "hash", "tag", "adopted", "darkroom") if k in entry},
                        "take_files": sorted(tree(before / take))}
                if shot is None:
                    view["shot"] = "missing from the shot list"
                else:
                    t = tag or None
                    view["capture_hash_matches"] = m.capture_hash(shot, None if shot.prep else t) == entry.get("capture_hash")
                    if not shot.prep:
                        view["encode_hash_matches"] = m.encode_hash(shot, t) == entry.get("encode_hash")
                        view["expected_outputs"] = [str(p.relative_to(m.dir)) for p in expected_outputs(m, shot, t)]
                entries.append(view)
            changed = sorted(set(tree(before)) ^ set(tree(project)))
            summary = {"loaded": True, "failed": failed, "new_keys": sorted(set(after) - set(state)),
                       "files_added_or_removed": changed}
        finally:
            shutil.rmtree(project)
            shutil.copytree(before, project)
    return entries, summary


def read(root):
    projects = sorted(p for p in (Path(root) / "projects").iterdir() if (p / "media.json").is_file())
    saves, summaries = [], {}
    for p in projects:
        entries, summaries[p.name] = describe(p)
        saves += entries
    print(json.dumps({"saves": saves, "projects": summaries}, sort_keys=True))


def append(root):
    project = Path(root) / "projects" / "appended"
    if project.exists():
        raise SystemExit("append target already exists")
    with tempfile.TemporaryDirectory() as tmp:
        isolate(tmp)
        shoot_or_die(lay_down(project, APPENDED))


if __name__ == "__main__":
    mode, target = sys.argv[1], sys.argv[2]
    {"write": write, "read": read, "append": append}[mode](target)
