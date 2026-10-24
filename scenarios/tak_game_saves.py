"""Save-compatibility scenario for the text adventures built on tak whose game keeps one JSON save per
slot: Tidewater, Overwinter and Night Ferry (checkout mode).

The three share a save layer exactly. Each is a package `src/<game>/` whose `game.py` defines the
game class and whose `state.py` defines `SAVE_FILENAME` ("save.json"). The class builds its front-end
with `tak.ui.createUserInterface`, opens tak's save menu (`tak.saves.chooseSlot`) over a
`tak.saves.SaveFileManager` in `<ENV_PREFIX>_SAVE_DIR`, writes the whole run to the slot's save file in
`save()` (validated against `schemas/save.json`) after every scene, and reads it back in
`load(path)`, which sets `failedLoad` when it cannot. Only what is inside the file differs from game to
game, and this scenario never looks inside it except to compare it with what the version reads back.

  write DIR   the baseline is PLAYED: its own game class runs its own scenes, with a seeded bot
              (menu_bot.MenuBot) in place of the front-end, answering every menu with an option the
              game offers. Four sessions: three new games (seeds 1-3, 3 / 60 / 250 menus) and one that
              loads slot 2 and plays on (seed 4, 40 menus). Every byte of every save is the game's own
              `save()`. provenance.json beside the slots records the commit, version, tak version,
              Python and the sessions.
  read DIR    prints one JSON line. DIR is copied first, so nothing the version does can touch it.
              Every slot is listed through the version's own SaveFileManager and slot metadata. A
              readable slot is loaded with the version's own `load()` (the game's migration runs
              there, as it does for a player), then written back with the version's own `save()` into
              a scratch directory, which also validates it against the version's own schema. The view
              of a slot is that re-saved file PROJECTED onto the keys of the file on disk: every value
              the file holds, as the version reconstructs it. A key the version drops shows up as
              "<dropped>", a fact id it forgets as a shorter list. Keys the version adds, and the
              top-level "version" a migration raises, are left out, so a release that only adds is not
              blamed for adding.
  append DIR  the candidate is played into a new slot (seed 5, 40 menus).

APP_SOURCE is the checkout root (the gate's checkout mode sets it and runs from there).
"""

import contextlib
import glob
import importlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile

import menu_bot

SOURCE = os.path.abspath(os.environ.get("APP_SOURCE") or os.getcwd())
os.chdir(SOURCE)  # every game opens schemas/save.json relative to the working directory
sys.path.insert(0, os.path.join(SOURCE, "src"))
for key, value in (("DO_NOT_TRACK", "1"), ("TRACE_USAGE_REPORTING", "off")):
    os.environ[key] = value

SESSIONS = [  # (seed, menus, slot)
    (1, 3, "new"),
    (2, 60, "new"),
    (3, 250, "new"),
    (4, 40, ("load", 2)),
]
APPEND_SESSION = (5, 40, "new")
DROPPED = "<dropped>"


def find_package():
    found = [os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(SOURCE, "src", "*", "game.py"))
             if os.path.isfile(os.path.join(os.path.dirname(p), "state.py"))]
    if len(found) != 1:
        raise SystemExit(f"expected one src/<game>/ with game.py and state.py, found {found}")
    return found[0]


PACKAGE = find_package()
game_module = importlib.import_module(f"{PACKAGE}.game")
state_module = importlib.import_module(f"{PACKAGE}.state")
PREFIX = getattr(game_module, "ENV_PREFIX", PACKAGE.upper())
os.environ[f"{PREFIX}_USAGE_REPORTING_ENABLED"] = "false"
SAVE_FILENAME = state_module.SAVE_FILENAME


def game_class():
    found = [v for v in vars(game_module).values()
             if isinstance(v, type) and v.__module__ == game_module.__name__
             and all(callable(getattr(v, m, None)) for m in ("play", "save", "load"))]
    if len(found) != 1:
        raise SystemExit(f"expected one game class in {PACKAGE}.game, found {[c.__name__ for c in found]}")
    return found[0]


GAME = game_class()


def manager(root):
    from tak.saves import SaveFileManager
    return SaveFileManager(root, primaryFile=SAVE_FILENAME, readMetadata=game_module.slotMetadata)


def play(root, seed, budget, slot):
    """One session of the real game, saving into root."""
    os.environ[f"{PREFIX}_SAVE_DIR"] = root
    holder = {}

    def done():
        m = holder.get("manager")
        return bool(m and m.selected_save_slot is not None and os.path.isfile(m.get_save_path(SAVE_FILENAME)))

    def make_ui(interfaceType, prompt, header, *args, **kwargs):
        bot = menu_bot.MenuBot(prompt, header, seed=seed, budget=budget, slot=slot, done=done)
        holder["bot"] = bot
        return bot

    real = game_module.createUserInterface
    game_module.createUserInterface = make_ui
    real_init_manager = None
    try:
        # the manager the game builds is the one done() looks at
        import tak.saves as tak_saves
        real_init_manager = tak_saves.SaveFileManager.__init__

        def remember(self, *a, **k):
            real_init_manager(self, *a, **k)
            holder["manager"] = self

        tak_saves.SaveFileManager.__init__ = remember
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                game = GAME()
                game.play()
            except menu_bot.SessionOver:
                pass
    finally:
        game_module.createUserInterface = real
        if real_init_manager is not None:
            import tak.saves as tak_saves
            tak_saves.SaveFileManager.__init__ = real_init_manager
    if not done():
        raise SystemExit(f"session seed={seed} ended without the game writing {SAVE_FILENAME}")
    m = holder["manager"]
    return {"seed": seed, "menus": holder["bot"].turns, "slot": m.selected_save_slot}


def git_head():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=SOURCE, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def tak_version():
    try:
        import tak
        return getattr(tak, "__version__", None)
    except ImportError:
        return None


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def write(root):
    played = [dict(play(root, seed, budget, slot), session=slot if slot == "new" else f"load slot {slot[1]}")
              for seed, budget, slot in SESSIONS]
    with open(os.path.join(root, "provenance.json"), "w") as f:
        json.dump({"scenario": "scenarios/tak_game_saves.py", "game": PACKAGE, "commit": git_head(),
                   "version": read_text(os.path.join(SOURCE, "version.txt")), "tak": tak_version(),
                   "python": platform.python_version(), "sessions": played,
                   "how": "each save written by the game's own save() while a seeded bot played its menus"},
                  f, indent=2, sort_keys=True)


class _Scratch:
    """Stands in for the game's SaveFileManager inside save(): one path in a scratch directory."""

    def __init__(self, directory):
        self.directory = directory

    def get_save_path(self, name):
        return os.path.join(self.directory, name)


def project(saved, recorded):
    """`saved` (what the version re-saves) cut to the shape of `recorded` (the file on disk)."""
    if isinstance(recorded, dict):
        if not isinstance(saved, dict):
            return {k: DROPPED for k in recorded} if saved is None else saved
        return {k: project(saved[k], v) if k in saved else DROPPED for k, v in recorded.items()}
    if isinstance(recorded, list) and isinstance(saved, list) and len(saved) == len(recorded):
        return [project(s, r) for s, r in zip(saved, recorded)]
    return saved


def slot_view(path):
    with open(path, encoding="utf-8") as f:
        recorded = json.load(f)
    game = GAME.__new__(GAME)
    game.failedLoad = None
    with contextlib.redirect_stdout(io.StringIO()):
        game.load(path)
    if game.failedLoad:
        return {"loaded": False}
    with tempfile.TemporaryDirectory() as scratch:
        game.saveFileManager = _Scratch(scratch)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                game.save()
        except Exception as e:  # noqa: BLE001 — a save the version refuses is itself a fact
            return {"loaded": True, "resaved": f"refused:{type(e).__name__}"}
        with open(os.path.join(scratch, SAVE_FILENAME), encoding="utf-8") as f:
            saved = json.load(f)
    if isinstance(recorded, dict):
        recorded = {k: v for k, v in recorded.items() if k != "version"}
    return {"loaded": True, "resaved": "valid", "data": project(saved, recorded)}


def read(root):
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "saves")
        shutil.copytree(root, copy)
        m = manager(copy)
        saves = []
        for s in m.list_save_files():
            unreadable = bool((s.get("metadata") or {}).get("unreadable"))
            entry = {"slot": s["slot"], "slot_name": s["slot_name"], "metadata": {"unreadable": unreadable}}
            if not unreadable:
                m.select_save_slot(s["slot"])
                entry.update(slot_view(m.get_save_path(SAVE_FILENAME)))
                entry["validation"] = "valid" if entry.get("resaved") == "valid" else "invalid"
            saves.append(entry)
        view = {"saves": saves}
        if os.environ.get("TAK_PRIMARY_FILE"):  # a curated fixture: the counts a manifest promises
            view["next_slot"] = m.get_next_available_slot()
    print(json.dumps(view, sort_keys=True))


def append(root):
    seed, budget, slot = APPEND_SESSION
    play(root, seed, budget, slot)


if __name__ == "__main__":
    mode, directory = sys.argv[1], os.path.abspath(sys.argv[2])
    {"write": write, "read": read, "append": append}[mode](directory)
