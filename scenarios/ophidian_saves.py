"""Save-compatibility scenario for ophidian (https://github.com/Preponderous-Software/ophidian), checkout mode.

ophidian keeps one save, save.json, holding the player's progress across runs: the ophidian's name,
currency, ascension level, cosmetics, upgrades bought, lifetime stats and an obituary for every run
(`src/progression/save.py`, `SaveManager`, with `migrateSaveData` and `SAVE_VERSION`). One copy of
the game has one save, so here a save is a directory holding one save.json. Versions from before that
module existed keep no saves at all: such a version writes none and has none to read.

  write DIR   the baseline is PLAYED: its own Ophidian class runs its own text-UI game loop, with a
              seeded bot pressing its keys (turning the snake, now and then restarting a run or cycling
              a cosmetic; never quitting or opening the shop) and the frame delay and drawing switched
              off. Each session ends after a number of ticks the way a player quitting ends it: the
              game records the run and saves. Four sessions: 30, 300 and 1500 ticks on fresh saves
              (seeds 1-3), and 300 more ticks continuing the second save (seed 4). Every byte of every
              save.json is the game's own SaveManager. provenance.json records the commit, version,
              Python and the sessions.
  read DIR    prints one JSON line. Each save is copied to a scratch file and loaded with the version's
              own SaveManager (its migration runs there, as at launch). The view is what it loaded,
              projected onto the keys of the file on disk: every value the file holds, as the version
              holds it ("<dropped>" for a key the version no longer keeps). Keys a version adds, and
              the top-level "version" a migration raises, are left out.
  append DIR  the candidate is played as a new installation (seed 5, 300 ticks).
"""

import contextlib
import io
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import time

SOURCE = os.path.abspath(os.environ.get("APP_SOURCE") or os.getcwd())
os.chdir(SOURCE)
sys.path.insert(0, os.path.join(SOURCE, "src"))
for key, value in (("DO_NOT_TRACK", "1"), ("TRACE_USAGE_REPORTING", "off"), ("SDL_VIDEODRIVER", "dummy"),
                   ("SDL_AUDIODRIVER", "dummy"), ("PYGAME_HIDE_SUPPORT_PROMPT", "1")):
    os.environ[key] = value

KEEPS_SAVES = os.path.isfile(os.path.join(SOURCE, "src", "progression", "save.py"))
SAVE = "save.json"
DROPPED = "<dropped>"
SESSIONS = [("run-1", 1, 30, None), ("run-2", 2, 300, None), ("run-3", 3, 1500, None),
            ("run-2-continued", 4, 300, "run-2")]
APPEND = ("added", 5, 300, None)
KEYS = ["w", "a", "s", "d"] * 6 + [None] * 20 + ["r", "c"]


def play(root, name, seed, ticks, start_from):
    from progression import save as save_mod
    import ophidian as game_mod
    from textui.textrenderer import TextRenderer

    target_dir = os.path.join(root, name)
    os.makedirs(target_dir)
    target = os.path.join(target_dir, SAVE)
    if start_from:
        shutil.copy2(os.path.join(root, start_from, SAVE), target)
    rng = random.Random(seed)
    random.seed(seed)       # the game's own randomness (food, names), so a session replays
    patches = []

    def patch(obj, attr, value):
        patches.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, value)

    patch(save_mod, "DEFAULT_SAVE_PATH", target)
    for attr in dir(TextRenderer):
        if attr.startswith(("render", "clearScreen")) or attr in ("enableRawMode", "disableRawMode"):
            patch(TextRenderer, attr, lambda self, *a, **k: None)
    holder = {"frames": 0}

    def key_press(self, timeout=0):
        # the text loop asks for a key once per tick in every version: the tick budget is counted
        # here, and spending it ends the loop the way a player quitting does
        holder["frames"] += 1
        if holder["frames"] >= ticks and "game" in holder:
            holder["game"].running = False
            return None
        return rng.choice(KEYS)
    patch(TextRenderer, "getKeyPress", key_press)
    patch(time, "sleep", lambda seconds: None)
    real_init = game_mod.Ophidian.__init__

    def init(self, *a, **k):
        holder["game"] = self
        real_init(self, *a, **k)
    patch(game_mod.Ophidian, "__init__", init)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                game_mod.Ophidian(useTextUI=True).run()
            except SystemExit:
                pass        # quitApplication() ends with quit(), as when a player quits
    finally:
        for obj, attr, value in reversed(patches):
            setattr(obj, attr, value)
    if not os.path.isfile(target):
        raise SystemExit(f"session {name}: the game wrote no {SAVE}")
    return {"save": name, "seed": seed, "ticks": ticks, "startedFrom": start_from}


def git_head():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=SOURCE, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def write(root):
    played = [play(root, *s) for s in SESSIONS] if KEEPS_SAVES else []
    try:
        version = open(os.path.join(SOURCE, "version.txt")).read().strip()
    except OSError:
        version = None
    with open(os.path.join(root, "provenance.json"), "w") as f:
        json.dump({"scenario": "scenarios/ophidian_saves.py", "commit": git_head(), "version": version,
                   "keepsSaves": KEEPS_SAVES, "python": platform.python_version(), "sessions": played,
                   "how": "each save.json written by the game's own SaveManager while a seeded bot pressed its keys"
                   if KEEPS_SAVES else "this version keeps no saves"}, f, indent=2, sort_keys=True)


def project(saved, recorded):
    if isinstance(recorded, dict):
        if not isinstance(saved, dict):
            return {k: DROPPED for k in recorded} if saved is None else saved
        return {k: project(saved[k], v) if k in saved else DROPPED for k, v in recorded.items()}
    if isinstance(recorded, list) and isinstance(saved, list) and len(saved) == len(recorded):
        return [project(s, r) for s, r in zip(saved, recorded)]
    return saved


def save_view(path):
    if not KEEPS_SAVES:
        return {"loaded": "this version keeps no saves"}
    from progression.save import SaveManager
    with open(path) as f:
        recorded = json.load(f)
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, SAVE)
        shutil.copy2(path, copy)
        with contextlib.redirect_stdout(io.StringIO()):
            data = SaveManager(path=copy).data
    if isinstance(recorded, dict):
        recorded = {k: v for k, v in recorded.items() if k != "version"}
    return {"loaded": True, "data": project(json.loads(json.dumps(data)), recorded)}


def read(root):
    saves = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name, SAVE)
        if os.path.isfile(path):
            saves.append(dict(save_view(path), name=name))
    print(json.dumps({"saves": saves}, sort_keys=True))


def append(root):
    if not KEEPS_SAVES:
        raise SystemExit("this version keeps no saves; it cannot add one")
    play(root, *APPEND)


if __name__ == "__main__":
    mode, directory = sys.argv[1], os.path.abspath(sys.argv[2])
    {"write": write, "read": read, "append": append}[mode](directory)
