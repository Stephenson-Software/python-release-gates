"""Save-compatibility scenario for FishE (https://github.com/Stephenson-Software/FishE), checkout mode.

A FishE save is three files, player.json, stats.json and timeService.json, written by the game's own
JSON reader-writers after every action. Where they live depends on the version:

  flat    2.x and earlier: one save, directly in the data directory (`data/` beside the checkout).
  slots   since FishE moved onto tak: numbered slot_N directories under the data directory
          (`src/saveFileManager.py`, tak's SaveFileManager with player.json as the primary file).
          At launch the game moves a flat save into a slot (`migrate_old_save_files`), which is how
          a player upgrading from a flat version keeps their run.

  write DIR   the baseline is PLAYED: its own FishE class runs its own locations with a seeded bot
              (menu_bot.MenuBot) in place of its user interface. A flat version gets one new game
              (seed 1, 80 menus) and then a second session that reopens it (seed 2, 40 menus), and its
              files are copied from its data directory into DIR unchanged. A slots version plays into
              DIR: three new games (seeds 1-3, 5 / 60 / 200 menus) and one session that loads slot 2
              (seed 4, 40 menus). Every byte is the game's own. provenance.json records the commit,
              version, layout, Python and sessions.
  read DIR    prints one JSON line. DIR is copied first. A slots version first does what it does at
              launch (moves a flat save into a slot), then lists the slots through its own manager;
              a flat version reads its one save. Each save is loaded with the version's own readers, as
              the game loads it at start, and each file is written back with the version's own writer
              (`createJsonFrom…`). The view of a save is that JSON projected onto the keys of the file
              on disk: every value the file holds, as the version reconstructs it ("<dropped>" for a key
              the version no longer keeps). Keys a version adds are left out. Saves are listed by
              content, not by slot number, because the move into a slot picks the number.
  append DIR  the candidate is played as a player starting a new game would: its own launch runs
              first (including moving a flat save into a slot), then a new game is played into the
              next free slot (seed 5, 40 menus).

With a curated fixture (TAK_PRIMARY_FILE set by the gate) the view also carries next_slot, the count a
fixture manifest promises.
"""

import builtins
import contextlib
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
os.chdir(SOURCE)  # FishE opens schemas/*.json relative to the working directory
sys.path.insert(0, os.path.join(SOURCE, "src"))
for key, value in (("DO_NOT_TRACK", "1"), ("TRACE_USAGE_REPORTING", "off"),
                   ("FISHE_USAGE_REPORTING_ENABLED", "false"), ("SDL_VIDEODRIVER", "dummy"),
                   ("SDL_AUDIODRIVER", "dummy")):
    os.environ[key] = value

FILES = ("player.json", "stats.json", "timeService.json")
# The one layout move the gate accepts (files-kept-3): a slots-era FishE, launched by `append` over a
# flat 2.x save, moves it into slot_1 (`migrate_old_save_files`). Accepted by the owner on
# 2026-10-24 on these terms: each file must arrive at its new path byte-identical, and read-3 must
# still report every value as the baseline did. Nothing else may move.
SAVE_MIGRATIONS = {
    "player.json": "slot_1/player.json",
    "stats.json": "slot_1/stats.json",
    "timeService.json": "slot_1/timeService.json",
}
LAYOUT = "slots" if os.path.isfile(os.path.join(SOURCE, "src", "saveFileManager.py")) else "flat"
DROPPED = "<dropped>"
ABSENT = "<absent>"
SLOT_SESSIONS = [(1, 5, "new"), (2, 60, "new"), (3, 200, "new"), (4, 40, ("load", 2))]
FLAT_SESSIONS = [(1, 80, None), (2, 40, None)]
APPEND_SESSION = (5, 40, "new")

with contextlib.redirect_stdout(io.StringIO()):
    import fishE  # noqa: E402 — the game module, from the checkout
from player.playerJsonReaderWriter import PlayerJsonReaderWriter  # noqa: E402
from stats.statsJsonReaderWriter import StatsJsonReaderWriter  # noqa: E402
from world.timeServiceJsonReaderWriter import TimeServiceJsonReaderWriter  # noqa: E402


@contextlib.contextmanager
def quiet_play():
    """The game prints its screens and, in flat versions, waits on input() for [ CONTINUE ]."""
    real_input = builtins.input
    builtins.input = lambda *a, **k: "1"  # [ CONTINUE ] takes anything; a bet or an amount takes a number
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield
    finally:
        builtins.input = real_input


def play(root, seed, budget, slot, cwd=None):
    """One session of the real game. A slots version saves into root; a flat version into cwd/data."""
    holder = {}

    def done():
        if LAYOUT == "flat":
            return os.path.isfile(os.path.join(cwd, "data", "player.json"))
        m = holder.get("manager")
        return bool(m and m.selected_save_slot is not None and os.path.isfile(m.get_save_path("player.json")))

    patches = []

    def patch(obj, name, value):
        patches.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    if LAYOUT == "flat":
        def make_ui(currentPrompt, timeService, player):
            b = menu_bot.MenuBot(currentPrompt, seed=seed, budget=budget, done=done)
            b.timeService, b.player = timeService, player
            holder["bot"] = b
            return b
        patch(fishE, "UserInterface", make_ui)
    else:
        os.environ["FISHE_SAVE_DIR"] = root

        def make_ui(interfaceType, prompt, timeService, player, *a, **k):
            b = menu_bot.MenuBot(prompt, seed=seed, budget=budget, slot=slot, done=done)
            b.timeService, b.player = timeService, player
            holder["bot"] = b
            return b
        patch(fishE.UserInterfaceFactory, "create_user_interface", staticmethod(make_ui))
        import saveFileManager
        real_init = saveFileManager.SaveFileManager.__init__

        def remember(self, *a, **k):
            real_init(self, *a, **k)
            holder["manager"] = self
        patch(saveFileManager.SaveFileManager, "__init__", remember)
    here = os.getcwd()
    try:
        if cwd:
            os.chdir(cwd)
        with quiet_play():
            try:
                game = fishE.FishE()
                game.play()
            except menu_bot.SessionOver:
                pass
    finally:
        os.chdir(here)
        for obj, name, value in reversed(patches):
            setattr(obj, name, value)
    if not done():
        raise SystemExit(f"session seed={seed} ended without FishE writing player.json")
    out = {"seed": seed, "menus": holder["bot"].turns}
    if LAYOUT == "slots":
        out["slot"] = holder["manager"].selected_save_slot
    return out


def git_head():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=SOURCE, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def write(root):
    if LAYOUT == "flat":
        with tempfile.TemporaryDirectory() as tmp:
            # the flat versions read and write data/ relative to the working directory; their schemas
            # (if any) are read from the checkout, so the checkout's files are linked in beside it
            for name in os.listdir(SOURCE):
                if name not in ("data", ".git"):
                    os.symlink(os.path.join(SOURCE, name), os.path.join(tmp, name))
            played = [play(root, seed, budget, None, cwd=tmp) for seed, budget, _ in FLAT_SESSIONS]
            for name in FILES:
                src = os.path.join(tmp, "data", name)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(root, name))
    else:
        played = [play(root, seed, budget, slot) for seed, budget, slot in SLOT_SESSIONS]
    try:
        version = open(os.path.join(SOURCE, "version.txt")).read().strip()
    except OSError:
        version = None
    with open(os.path.join(root, "provenance.json"), "w") as f:
        json.dump({"scenario": "scenarios/fishe_saves.py", "commit": git_head(), "version": version,
                   "layout": LAYOUT, "python": platform.python_version(), "sessions": played,
                   "how": "each save written by FishE's own save() while a seeded bot played its menus"},
                  f, indent=2, sort_keys=True)


def project(saved, recorded):
    if isinstance(recorded, dict):
        if not isinstance(saved, dict):
            return {k: DROPPED for k in recorded} if saved is None else saved
        return {k: project(saved[k], v) if k in saved else DROPPED for k, v in recorded.items()}
    if isinstance(recorded, list) and isinstance(saved, list) and len(saved) == len(recorded):
        return [project(s, r) for s, r in zip(saved, recorded)]
    return saved


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_view(directory):
    """One save (the three files in `directory`), as this version reconstructs it."""
    recorded = {name: read_json(os.path.join(directory, name)) for name in FILES}
    present = {name: os.path.isfile(os.path.join(directory, name)) for name in FILES}
    pw, sw, tw = PlayerJsonReaderWriter(), StatsJsonReaderWriter(), TimeServiceJsonReaderWriter()
    failed = []
    with quiet_play():
        if LAYOUT == "slots":
            # what FishE.__init__ does: defaults first, each present file loaded over them, a file
            # that fails recorded (and the default kept) — the loaders are the game's own methods
            game = fishE.FishE.__new__(fishE.FishE)
            game.config = fishE.Config()
            game.playerJsonReaderWriter, game.statsJsonReaderWriter, game.timeServiceJsonReaderWriter = pw, sw, tw
            game.failedLoads = []

            class One:
                def get_save_path(self, name):
                    return os.path.join(directory, name)
            game.saveFileManager = One()
            game.player = fishE.Player(game.config)
            game.stats = fishE.Stats()
            game.timeService = fishE.TimeService(game.player, game.stats)
            if present["player.json"]:
                game.loadPlayer()
            if present["stats.json"]:
                game.loadStats()
            if present["timeService.json"]:
                game.loadTimeService()
            failed = sorted(f.split(" ", 1)[0] for f in game.failedLoads)
            player, stats, time_service = game.player, game.stats, game.timeService
        else:
            player = stats = time_service = None
            try:
                with open(os.path.join(directory, "player.json")) as f:
                    player = pw.readPlayerFromFile(f)
            except Exception:  # noqa: BLE001
                failed.append("player.json")
            try:
                with open(os.path.join(directory, "stats.json")) as f:
                    stats = sw.readStatsFromFile(f)
            except Exception:  # noqa: BLE001
                failed.append("stats.json")
            try:
                with open(os.path.join(directory, "timeService.json")) as f:
                    time_service = tw.readTimeServiceFromFile(f, player, stats)
            except Exception:  # noqa: BLE001
                failed.append("timeService.json")
        rebuilt = {}
        for name, obj, fn in (("player.json", player, pw.createJsonFromPlayer),
                              ("stats.json", stats, sw.createJsonFromStats),
                              ("timeService.json", time_service, tw.createJsonFromTimeService)):
            try:
                rebuilt[name] = fn(obj) if obj is not None else None
            except Exception as e:  # noqa: BLE001
                rebuilt[name] = {"error": type(e).__name__}
    files = {name: (project(rebuilt[name], recorded[name]) if present[name] else ABSENT) for name in FILES}
    return {"files": files, "loadFailed": failed, "metadata": {"unreadable": False},
            "validation": "valid" if not failed else "invalid"}


def read(root):
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "saves")
        shutil.copytree(root, copy)
        saves = []
        next_slot = None
        if LAYOUT == "slots":
            import saveFileManager
            m = saveFileManager.SaveFileManager(data_directory=copy)
            with quiet_play():
                m.migrate_old_save_files()   # what the game does at launch
            for s in m.list_save_files():
                if (s.get("metadata") or {}).get("unreadable"):
                    saves.append({"metadata": {"unreadable": True}})
                    continue
                m.select_save_slot(s["slot"])
                saves.append(save_view(os.path.dirname(m.get_save_path("player.json"))))
            next_slot = m.get_next_available_slot()
        elif os.path.isfile(os.path.join(copy, "player.json")):
            saves.append(save_view(copy))
        saves.sort(key=lambda s: json.dumps(s, sort_keys=True))
        view = {"saves": saves}
        if os.environ.get("TAK_PRIMARY_FILE"):
            view["next_slot"] = next_slot
    print(json.dumps(view, sort_keys=True))


def append(root):
    if LAYOUT == "flat":
        raise SystemExit("a flat FishE keeps one save; it cannot add one beside another")
    seed, budget, slot = APPEND_SESSION
    play(root, seed, budget, slot)


if __name__ == "__main__":
    mode, directory = sys.argv[1], os.path.abspath(sys.argv[2])
    {"write": write, "read": read, "append": append}[mode](directory)
