"""Save-compatibility scenario for Cave (https://github.com/Stephenson-Software/Cave-Console-Game), checkout
mode with source_root `.`.

Cave is one script, maincode.py, played with input()/print(). Its save is one file, savefile.txt,
beside the script: the name of the decision the player stopped at (`SAVE` at any question), or empty
for a game started fresh. One copy of the game has one save, so here a save is a directory holding one
savefile.txt: one installation of the game.

  write DIR   the baseline is PLAYED: its own maincode.py runs in a scratch directory (so the save it
              writes beside itself lands there, never in the checkout) and is answered on stdin. Five
              sessions: stop and save at the first, second and third question; play a new game to the
              end; and reopen the first save, answer one question and save again. Each savefile.txt is
              the game's own `savegame()` output, copied into DIR/<session>/ unchanged.
              provenance.json records the commit, version, Python and the answers given.
  read DIR    prints one JSON line. For every save, a scratch copy of it is put beside a scratch copy
              of the version's own maincode.py, and the game is started and told it has a save. The
              view is what it does with it: resumed (and at which question), started a new game, or
              said the save could not be read. Which question it resumed at is told by comparing it
              with the questions the same version asks in a fresh game, so a reworded question is not
              mistaken for a lost save. DIR itself is never handed to the game.
  append DIR  the candidate is played as a new installation that saves at the second question.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile

SOURCE = os.path.abspath(os.environ.get("APP_SOURCE") or os.getcwd())
GAME = os.path.join(SOURCE, "maincode.py")
SAVE = "savefile.txt"
SESSIONS = [  # (directory, answers, start from)
    ("stop-1", ["NO", "SAVE", ""], None),
    ("stop-2", ["NO", "YES", "SAVE", ""], None),
    ("stop-3", ["NO", "YES", "YES", "SAVE", ""], None),
    ("finished", ["NO", "YES", "YES", "LEAVE", ""], None),
    ("resumed", ["YES", "YES", "SAVE", ""], "stop-1"),
]
APPEND = ("added", ["NO", "YES", "SAVE", ""], None)


def run_game(directory, answers):
    """The version's own maincode.py, copied into `directory` and run there with `answers` on stdin."""
    shutil.copy2(GAME, os.path.join(directory, "maincode.py"))
    proc = subprocess.run([sys.executable, "maincode.py"], cwd=directory, input="\n".join(answers) + "\n",
                          capture_output=True, text=True, timeout=60)
    os.remove(os.path.join(directory, "maincode.py"))
    return proc.stdout


def play(root, name, answers, start_from):
    with tempfile.TemporaryDirectory() as tmp:
        if start_from:
            shutil.copy2(os.path.join(root, start_from, SAVE), os.path.join(tmp, SAVE))
        run_game(tmp, answers)
        if not os.path.isfile(os.path.join(tmp, SAVE)):
            raise SystemExit(f"session {name}: the game wrote no {SAVE}")
        os.makedirs(os.path.join(root, name))
        shutil.copy2(os.path.join(tmp, SAVE), os.path.join(root, name, SAVE))


def git_head():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=SOURCE, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def write(root):
    for name, answers, start in SESSIONS:
        play(root, name, answers, start)
    try:
        version = open(os.path.join(SOURCE, "version.txt")).read().strip()
    except OSError:
        version = None
    with open(os.path.join(root, "provenance.json"), "w") as f:
        json.dump({"scenario": "scenarios/cave_saves.py", "commit": git_head(), "version": version,
                   "python": platform.python_version(),
                   "sessions": [{"save": n, "answers": a, "startedFrom": s} for n, a, s in SESSIONS],
                   "how": "each savefile.txt written by the game's own savegame() while its questions were "
                          "answered on stdin"}, f, indent=2, sort_keys=True)


def questions(output):
    return [line.strip() for line in output.splitlines() if line.strip().endswith("?")]


def fresh_questions():
    """The questions this version asks, in order, on the way through a fresh game."""
    with tempfile.TemporaryDirectory() as tmp:
        asked = questions(run_game(tmp, ["NO", "YES", "YES"]))
    return asked[1:]   # the first one is "Do you have a save file?"


def save_view(save_dir, asked):
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy2(os.path.join(save_dir, SAVE), os.path.join(tmp, SAVE))
        out = run_game(tmp, ["YES"])
    after = out.split("save file? YES or NO?", 1)[-1]
    if "Loading up your save" in after:
        q = questions(after)
        return {"outcome": "resumed", "question": asked.index(q[0]) + 1 if q and q[0] in asked else "unknown"}
    if "doesn't look like you have a save" in after:
        return {"outcome": "new-game"}
    if "couldn't be read" in after:
        return {"outcome": "unreadable"}
    return {"outcome": "other", "said": after.strip()[:200]}


def read(root):
    asked = fresh_questions()
    saves = []
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, SAVE)):
            saves.append(dict(save_view(d, asked), name=name))
    print(json.dumps({"saves": saves}, sort_keys=True))


def append(root):
    play(root, *APPEND)


if __name__ == "__main__":
    mode, directory = sys.argv[1], os.path.abspath(sys.argv[2])
    {"write": write, "read": read, "append": append}[mode](directory)
