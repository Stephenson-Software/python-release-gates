"""A seeded player for menu-driven games, shared by the game save scenarios.

It stands in for a game's front-end (the object a tak game gets from `tak.ui.createUserInterface`,
or FishE's own user interface) and plays the game through its real menus: at every menu it picks
one of the options the game offers and does not refuse, with a `random.Random(seed)`, so the same
seed replays the same choices against the same version. Options that would leave the game or
destroy a save (quit, exit, retire, delete) are never picked. The game itself saves, through its
own code, whenever it normally would. A session ends when the bot has answered `budget` menus and
the game has written its save (`done()`): the bot then raises `SessionOver`, which stands for the
player closing the window. Nothing about a save's contents is written by the bot.

It is duck-typed rather than a subclass of tak's BaseUserInterface, because it also stands in for
front-ends that predate tak (FishE 2.x). A method it does not define answers with a no-op.
"""

import random
import re

# Never picked: leaving the game, retiring a character, or deleting a save.
AVOID = re.compile(r"\b(quit|exit|retire|delete|leave the game|main menu)\b", re.I)
SLOT_NEW = re.compile(r"^Create New Save", re.I)
SLOT_LOAD = re.compile(r"^Load Slot (\d+)", re.I)


class SessionOver(Exception):
    """The session's menu budget is spent and the game has saved."""


class StuckSession(Exception):
    """The hard cap was reached without the game ever saving."""


class MenuBot:
    def __init__(self, prompt=None, header=None, *, seed, budget, slot="new", done=lambda: True,
                 text_answers=("Ada", "3", "1", "yes")):
        self.currentPrompt = prompt
        self._header = header
        self.rng = random.Random(seed)
        self.budget = budget
        self.slot = slot              # "new", or ("load", N) for the save menu
        self.done = done
        self.turns = 0
        self.cap = budget * 4 + 400
        self.text_answers = list(text_answers)
        self.menus = 0
        self._picture = None

    # --- what the games call ------------------------------------------------------------
    def header(self):
        return self._header() if callable(self._header) else None

    def unavailableReasons(self, optionList, unavailableOptions):
        reasons = [None] * len(optionList)
        for number, reason in (unavailableOptions or {}).items():
            if 1 <= number <= len(optionList):
                reasons[number - 1] = reason
        if optionList and all(r is not None for r in reasons):
            return [None] * len(optionList)
        return reasons

    def selectableNumbers(self, reasons):
        return {str(i + 1) for i, r in enumerate(reasons) if r is None}

    def showOptions(self, descriptor, optionList, unavailableOptions=None, *args, **kwargs):
        self.menus += 1
        options = [str(o) for o in optionList]
        reasons = self.unavailableReasons(options, unavailableOptions)
        if callable(self._header):
            self._header()     # a real front-end draws the header on every menu
        slot_menu = any(SLOT_NEW.match(o) or SLOT_LOAD.match(o) for o in options)
        if slot_menu:
            for i, label in enumerate(options):
                if self.slot == "new" and SLOT_NEW.match(label):
                    return str(i + 1)
                m = SLOT_LOAD.match(label)
                if isinstance(self.slot, tuple) and m and int(m.group(1)) == self.slot[1]:
                    return str(i + 1)
            raise StuckSession(f"the save menu offers no {self.slot!r}: {options}")
        self.turns += 1
        if self.turns > self.budget and self.done():
            raise SessionOver()
        if self.turns > self.cap:
            raise StuckSession(f"{self.turns} menus answered and the game has not saved")
        choices = [i for i, (o, r) in enumerate(zip(options, reasons)) if r is None and not AVOID.search(o)]
        if not choices:
            choices = [i for i, (o, r) in enumerate(zip(options, reasons)) if r is None
                       and not re.search(r"\bdelete\b", o, re.I)]
        return str(self.rng.choice(choices) + 1)

    def showInteractiveDialogue(self, npc):
        """tak's default conversation, played through showOptions (BaseUserInterface's)."""
        while True:
            options = npc.get_dialogue_options()
            if not options:
                self.showDialogue(npc.introduce())
                return
            questions = [o.get("question", "Option %d" % (i + 1)) for i, o in enumerate(options)]
            questions.append("[Back]")
            choice = int(self.showOptions("Talking with %s" % npc.name, questions))
            if choice == len(questions):
                return
            self.showDialogue("%s: %s" % (npc.name, npc.get_dialogue_response(choice - 1)))

    def promptForText(self, promptText=""):
        return self.text_answers[self.rng.randrange(len(self.text_answers))]

    def promptForNumber(self, promptText=""):
        return float(self.rng.choice((1, 2, 3, 5, 10)))

    def timedKeyPress(self, message=""):
        return 0.5

    def setPicture(self, key, alt=None):
        self._picture = (key, alt)

    def picture(self):
        return self._picture

    def showDialogue(self, text=""):
        if self.currentPrompt is not None and hasattr(self.currentPrompt, "reset"):
            self.currentPrompt.reset()

    def __getattr__(self, name):
        # lotsOfSpace, divider, cleanup, showBusy and whatever a version adds that draws
        # something: nothing to draw here.
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **k: None
