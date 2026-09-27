"""Save-compatibility scenario for roam (https://github.com/Preponderous-Software/roam).

A roam save is one directory per world under the saves root (the save menu lists every
directory there): playerLocation.json, playerAttributes.json, playerInventory.json,
stats.json, tick.json, codex.json, goals.json and rooms/room_X_Y.json (versions with
underground levels add rooms/room_X_Y_Z.json for Z != 0). This scenario plays the game
through roam's own classes (the DI container, Map/RoomFactory world generation, Player,
Inventory, WorldScreenPersistence and the stats/tick/codex/goals readers-writers), never
hand-written JSON:

  fresh      a world just started: the spawn room, full energy, empty inventory
  explorer   a walk through seven rooms, each saved on leaving; energy 64; wood, stone and
             grass gathered; floors, a torch, a chest (with items in it) and every oak log
             placed, which leaves an emptied slot between full ones; an apple eaten; ticks,
             stats, codex and goals advanced
  hoarder    a full inventory (all 25 slots) of 21 item types, full stacks of 20 and
             partial ones, a live chicken among them

Modes: `write DIR` (baseline), `read DIR` (prints one JSON line), `append DIR` (candidate:
one more world, named the way the save menu names a new game; where the version has
underground levels it also goes down one level and saves there).

`read` copies DIR to a temporary directory and loads from the copy, so nothing the
loaders might write can touch DIR. It reports what the version RECONSTRUCTS, not the
JSON it reads: roam's loaders treat an unparseable file as absent and fall back to
defaults, so each fact is the loaded object's state (player room/location/energy,
inventory slots as the loaded Inventory holds them, every room file as the version's Map
loads it, summarised as entity class names per grid cell). Each file is also validated
against the reading version's own schemas/*.json; a save is "valid" only when all are.
The listing and names are the save menu's (SaveSelectionScreen); its "last played" label
is the directory mtime, not save data, and is left out. APP_SOURCE (or ROAM_SOURCE) is the
checkout root.

Inventories (the player's and every chest's) are judged against the file, not against the
other version: the view carries what was loaded as contents (item, count per slot, in any
order), and a `fidelity` block — `contents` (the loaded contents equal the file's) and
`positions` (every item is in the slot the file records). The gate requires the candidate's
fidelity to be true and compares everything else exactly, so an older release that packed
items forward on load (0.12.0 did) is not held against a candidate that restores them where
they were saved, while a candidate that moves, drops or changes an item is blocked.
"""

import inspect
import json
import os
import random
import re
import shutil
import sys
import tempfile
import uuid
from unittest import mock

CALLER_CWD = os.getcwd()
SOURCE = os.path.abspath((os.environ.get("APP_SOURCE") or os.environ.get("ROAM_SOURCE")) or CALLER_CWD)
os.chdir(SOURCE)  # roam opens schemas/*.json relative to the working directory
sys.path.insert(0, os.path.join(SOURCE, "src"))
for key, value in (("SDL_VIDEODRIVER", "dummy"), ("SDL_AUDIODRIVER", "dummy"),
                   ("PYGAME_HIDE_SUPPORT_PROMPT", "1"), ("ROAM_USAGE_REPORTING", "0"), ("DO_NOT_TRACK", "1"), ("LOG_LEVEL", "WARNING")):
    os.environ.setdefault(key, value)

import jsonschema  # noqa: E402 — roam's own dependency, used the way roam uses it
from bootstrap import createContainer  # noqa: E402
from codex.codex import Codex  # noqa: E402
from codex.codexJsonReaderWriter import CodexJsonReaderWriter  # noqa: E402
from config.config import Config  # noqa: E402
from entity.storableInventory import StorableInventory  # noqa: E402
from goals.goals import Goals  # noqa: E402
from goals.goalsJsonReaderWriter import GoalsJsonReaderWriter  # noqa: E402
from inventory.inventoryJsonReaderWriter import InventoryJsonReaderWriter  # noqa: E402
from player.player import Player  # noqa: E402
from rendering.renderer import Renderer  # noqa: E402
from screen.saveSelectionScreen import SaveSelectionScreen  # noqa: E402
from screen.worldScreenPersistence import WorldScreenPersistence  # noqa: E402
from stats.stats import Stats  # noqa: E402
from world.map import Map  # noqa: E402
from world.tickCounter import TickCounter  # noqa: E402

ROOM_FILE = re.compile(r"room_(-?\d+)_(-?\d+)(?:_(-?\d+))?\.json")
SCHEMA_FOR = {"playerLocation.json": "playerLocation.json", "playerAttributes.json": "playerAttributes.json",
              "playerInventory.json": "inventory.json", "stats.json": "stats.json", "tick.json": "tick.json",
              "codex.json": "codex.json", "goals.json": "goals.json"}


def takes(fn, name):
    return name in inspect.signature(fn).parameters


def entity_class(name):
    """An entity class by name, from the version's own modules (entity/ or entity/living/)."""
    module = name[0].lower() + name[1:]
    for package in ("entity", "entity.living"):
        try:
            return getattr(__import__(f"{package}.{module}", fromlist=[name]), name)
        except ImportError:
            continue
    raise LookupError(name)


def save_menu(root):
    """The version's save menu, pointed at root and sorted by name."""
    screen = SaveSelectionScreen(**{p: None for p in list(inspect.signature(SaveSelectionScreen.__init__).parameters)[1:]})
    screen.savesBaseDirectory = root
    screen.sortMode = SaveSelectionScreen.SORT_BY_NAME
    screen.refreshSaveCache()
    return screen


class World:
    """One world's game objects, wired the way the game wires them (tests/conftest.py)."""

    def __init__(self, root, name):
        self.config = Config()
        self.config.pathToSaveDirectory = os.path.join(root, name)
        c = createContainer(self.config)
        c.registerInstance(Renderer, mock.MagicMock())  # rooms keep a renderer; nothing is drawn
        self.player, self.stats, self.ticks = c.resolve(Player), c.resolve(Stats), c.resolve(TickCounter)
        self.persistence, self.map = c.resolve(WorldScreenPersistence), c.resolve(Map)
        self.codex, self.goals = c.resolve(Codex), c.resolve(Goals)
        self.inventoryRw = InventoryJsonReaderWriter(self.config)
        self.codexRw, self.goalsRw = CodexJsonReaderWriter(self.config), c.resolve(GoalsJsonReaderWriter)
        self.room, self.z = None, 0
        self.depth = takes(self.map.generateNewRoom, "z")

    # --- play -------------------------------------------------------------------------------
    def enter(self, x, y, z=0):
        at = (x, y, z) if self.depth else (x, y)
        room = self.map.getRoom(*at)
        if room == -1:
            room = self.map.generateNewRoom(*at)
            self.stats.incrementRoomsExplored()
        if self.room is not None:
            self.room.removeEntity(self.player)
            self.persistence.saveRoomToFile(self.room)
        half = self.config.gridSize // 2
        room.addEntityToLocation(self.player, room.getGrid().getLocationByCoordinates(half, half))
        for entity in room.getLivingEntities().values():
            if not isinstance(entity, Player):
                self.codex.discover(type(entity).__name__)
        self.room, self.z = room, z
        for _ in range(450):
            self.ticks.incrementTick()

    def gather(self, name, count):
        for location in list(self.room.getGrid().getLocations().values()):
            for entity in list(location.getEntities().values()):
                if count and type(entity).__name__ == name:
                    self.room.removeEntity(entity)
                    self.player.getInventory().placeIntoFirstAvailableInventorySlot(entity)
                    self.stats.incrementScore()
                    count -= 1

    def give(self, name, count):
        cls = entity_class(name)
        for _ in range(count):
            item = cls(self.ticks.getTick()) if "tickCreated" in inspect.signature(cls).parameters else cls()
            self.player.getInventory().placeIntoFirstAvailableInventorySlot(item)

    def place(self, slot, x, y):
        inventory = self.player.getInventory()
        inventory.setSelectedInventorySlotIndex(slot)
        item = inventory.removeSelectedItem()
        location = self.room.getGrid().getLocationByCoordinates(x, y)
        self.room.addEntityToLocation(item, location)
        return item

    def save(self):
        self.persistence.saveRoomToFile(self.room)
        save_location = self.persistence.savePlayerLocationToFile
        save_location(self.room, self.z) if takes(save_location, "currentZ") else save_location(self.room)
        self.persistence.savePlayerAttributesToFile()
        if not self.persistence.savePlayerInventoryToFile(self.inventoryRw):
            raise SystemExit("the inventory did not save")
        self.stats.save()
        self.ticks.save()
        self.codexRw.save(self.codex.getDiscoveredEntities())
        self.goals.evaluate()
        self.goalsRw.save(self.goals.getCompletedIdentifiers())

    # --- read -------------------------------------------------------------------------------
    def view(self):
        return {"location": fact(self.location_view), "energy": fact(self.energy_view),
                "inventory": fact(self.inventory_view),
                "progress": fact(self.progress_view), "rooms": fact(self.rooms_view)}

    def location_view(self):
        loaded = self.persistence.loadPlayerLocationFromFile(self.map)  # a room, or (room, z)
        room, z = loaded if isinstance(loaded, tuple) else (loaded, 0)
        return None if room is None else {"roomX": room.getX(), "roomY": room.getY(), "z": z,
                                          "locationId": str(self.player.getLocationID())}

    def energy_view(self):
        self.persistence.loadPlayerAttributesFromFile()
        return self.player.getEnergy()

    def inventory_view(self):
        self.persistence.loadPlayerInventoryFromFile(self.inventoryRw)
        inventory = self.player.getInventory()
        recorded = file_slots(read_json(os.path.join(self.config.pathToSaveDirectory, "playerInventory.json")))
        return dict(judged(slots_view(inventory), recorded), size=inventory.getNumInventorySlots(),
                    selected=inventory.getSelectedInventorySlotIndex())

    def progress_view(self):
        self.stats.load()
        self.ticks.load()
        codex = self.codexRw.load()
        return {"score": self.stats.getScore(), "roomsExplored": self.stats.getRoomsExplored(),
                "foodEaten": self.stats.getFoodEaten(), "deaths": self.stats.getNumberOfDeaths(),
                "tick": self.ticks.getTick(), "goals": sorted(self.goalsRw.load() or []),
                "codex": None if codex is None else sorted(codex)}

    def rooms_view(self):
        directory = self.config.getRoomsDirectory()
        rooms = {}
        for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
            match = ROOM_FILE.fullmatch(name)
            if not match:
                continue
            x, y, z = int(match[1]), int(match[2]), int(match[3] or 0)
            if z and not self.depth:
                rooms[name] = "no-underground-in-this-version"
                continue
            recorded = stored_slots(read_json(os.path.join(directory, name)))
            rooms[name] = fact(lambda: room_view(self.map.getRoom(*((x, y, z) if self.depth else (x, y))), recorded))
        return rooms


def fact(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — a loader that raises is itself a fact
        return {"error": type(e).__name__}


def read_json(path):
    """The file as JSON, straight from disk (never through the game), or None."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def file_slots(inventory_json):
    """[[slot, item class, count]] as the file records them; None when there is no readable record."""
    if not isinstance(inventory_json, dict) or not isinstance(inventory_json.get("inventorySlots"), list):
        return None
    out = []
    for slot in inventory_json["inventorySlots"]:
        contents = slot.get("slotContents") or [] if isinstance(slot, dict) else []
        if contents:
            out.append([slot.get("slotIndex"), (contents[0] or {}).get("entityClass"), len(contents)])
    return sorted(out, key=lambda s: (s[0] if isinstance(s[0], int) else -1, str(s[1])))


def stored_slots(room_json):
    """locationId → file_slots of every stored inventory (chest) in a room file."""
    found = {}

    def walk(node):
        if isinstance(node, dict):
            if "storedInventory" in node and "locationId" in node:
                found[str(node["locationId"])] = file_slots(node["storedInventory"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(room_json)
    return found


def judged(loaded, recorded):
    """Loaded slots as contents (order-free) plus fidelity to the file's record. With no
    readable record there is nothing to judge against; the file's validation says why."""
    view = {"contents": sorted([item, count] for _, item, count in loaded)}
    if recorded is not None:
        view["fidelity"] = {"contents": view["contents"] == sorted([item, count] for _, item, count in recorded),
                            "positions": sorted(loaded) == sorted(recorded)}
    return view


def slots_view(inventory):
    return [[i, type(slot.getContents()[0]).__name__, slot.getNumItems()]
            for i, slot in enumerate(inventory.getInventorySlots()) if not slot.isEmpty()]


def room_view(room, recorded=None):
    if room == -1:
        return "not-loaded"  # the version's Map found no file at its path, or could not parse it
    cells, stored = {}, {}
    for location in room.getGrid().getLocations().values():
        cell = f"{location.getX()},{location.getY()}"
        entities = [e for e in location.getEntities().values() if not isinstance(e, Player)]
        if entities:
            cells[cell] = sorted(type(e).__name__ for e in entities)
        for e in entities:
            if isinstance(e, StorableInventory):
                stored[cell] = judged(slots_view(e.getStoredInventory()),
                                      (recorded or {}).get(str(e.getLocationID())))
    return {"x": room.getX(), "y": room.getY(), "z": room.getZ() if hasattr(room, "getZ") else 0,
            "name": room.getName(), "background": list(room.getBackgroundColor()),
            "living": sorted(type(e).__name__ for e in room.getLivingEntities().values() if not isinstance(e, Player)),
            "cells": cells, "stored": stored}


def validate(save_dir):
    """Every JSON file in the save → its state against THIS version's schemas/*.json."""
    files, unreadable = {}, False
    for folder, _, names in os.walk(save_dir):
        for name in names:
            rel = os.path.relpath(os.path.join(folder, name), save_dir).replace(os.sep, "/")
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    data = json.load(f)
            except (ValueError, OSError):
                files[rel], unreadable = "unparseable", True
                continue
            schema = "room.json" if rel.startswith("rooms/") else SCHEMA_FOR.get(name)
            if schema is None or not os.path.exists(os.path.join("schemas", schema)):
                files[rel] = "no-schema"
                continue
            with open(os.path.join("schemas", schema), encoding="utf-8") as f:
                try:
                    jsonschema.validate(data, json.load(f))
                    files[rel] = "valid"
                except jsonschema.exceptions.ValidationError as e:
                    files[rel] = f"invalid:{e.validator}:{'/'.join(map(str, e.absolute_path))}"
    return dict(sorted(files.items())), unreadable


def seed(n):
    """Seed roam's world generation and make entity/room ids reproducible too."""
    random.seed(n)
    ids = random.Random(n)
    uuid.uuid4 = lambda: uuid.UUID(int=ids.getrandbits(128), version=4)


def play_fresh(root, name):
    w = World(root, name)
    w.enter(0, 0)
    w.save()


def play_explorer(root, name):
    w = World(root, name)
    w.enter(0, 0)
    for x, y in ((1, 0), (2, 0), (2, 1), (1, 1), (0, 1), (-1, 1)):
        w.enter(x, y)
        for kind in ("OakWood", "JungleWood", "Stone", "Grass", "Leaves", "Apple"):
            w.gather(kind, 3)
        w.save()
    w.give("WoodFloor", 3)
    w.give("Torch", 1)
    w.give("Chest", 1)
    inventory = w.player.getInventory()
    order = [type(s.getContents()[0]).__name__ for s in inventory.getInventorySlots() if not s.isEmpty()]
    for x, y in ((1, 1), (2, 1), (3, 1)):
        w.place(order.index("WoodFloor"), x, y)
    x = 5  # every oak log goes down as a wall: an emptied slot between full ones
    while not inventory.getInventorySlots()[order.index("OakWood")].isEmpty():
        w.place(order.index("OakWood"), x, 5)
        x += 1
    w.place(order.index("Torch"), 1, 2)
    inventory.setSelectedInventorySlotIndex(order.index("Apple"))
    inventory.removeSelectedItem()  # one apple eaten
    chest = w.place(order.index("Chest"), 3, 3)
    for kind, count in (("Stone", 5), ("Apple", 2)):
        for _ in range(count):
            chest.getStoredInventory().placeIntoFirstAvailableInventorySlot(entity_class(kind)())
    w.player.setEnergy(63.4)
    w.stats.incrementFoodEaten()
    w.save()


def play_hoarder(root, name):
    w = World(root, name)
    w.enter(0, 0)
    w.enter(0, -1)
    for kind, count in (("Stone", 60), ("OakWood", 57), ("Apple", 20), ("Banana", 7), ("CoalOre", 12),
                        ("IronOre", 5), ("JungleWood", 20), ("Leaves", 19), ("Grass", 3), ("WheatSeed", 14),
                        ("Wheat", 9), ("ChickenMeat", 2), ("BearMeat", 1), ("Torch", 20), ("WoodFloor", 20),
                        ("StoneFloor", 11), ("Fence", 8), ("Campfire", 1), ("Bed", 1), ("StoneBed", 1), ("Chicken", 1)):
        w.give(kind, count)
    w.player.getInventory().setSelectedInventorySlotIndex(4)
    w.player.setEnergy(88)
    w.save()


def write(root):
    os.makedirs(root, exist_ok=True)
    for i, (name, play) in enumerate((("fresh", play_fresh), ("explorer", play_explorer), ("hoarder", play_hoarder))):
        seed(1000 + i)
        play(root, name)


def append(root):
    menu = save_menu(root)
    name = menu._generateSaveName() if hasattr(menu, "_generateSaveName") else "save_1"
    seed(2000)
    w = World(root, name)
    w.enter(0, 0)
    w.enter(1, 0)
    w.gather("Stone", 4)
    w.save()
    if w.depth:  # the candidate's underground: room_1_0_-1.json, playerLocation roomZ -1
        w.enter(1, 0, -1)
        w.save()


def read(root):
    work = tempfile.mkdtemp(prefix="roam-read-")
    try:
        copy = os.path.join(work, "saves")
        shutil.copytree(root, copy)
        os.environ["ROAM_SAVE_DIR"] = copy
        saves = []
        for listed in save_menu(copy).getSaveDirectories():
            files, unreadable = validate(listed["path"])
            entry = {"name": listed["name"], "metadata": {"name": listed["name"], "unreadable": unreadable},
                     "files": files, "validation": "valid" if "valid" in files.values()
                     and not any(v == "unparseable" or v.startswith("invalid") for v in files.values()) else "invalid"}
            entry.update(World(copy, listed["name"]).view())
            saves.append(entry)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({"saves": saves, "next_slot": None}, sort_keys=True))


if __name__ == "__main__":
    mode, target = sys.argv[1], os.path.join(CALLER_CWD, sys.argv[2])
    {"write": write, "read": read, "append": append}[mode](target)
