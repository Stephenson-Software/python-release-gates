"""Save-compatibility scenario for tak (https://github.com/Stephenson-Software/tak).

tak owns the save SLOT layer — numbered `slot_N` directories under one data directory, the
listing and metadata the save menu shows, the next free slot, and JSON Schema validation —
while each game owns the format of the files inside a slot. This scenario therefore drives
only tak's public save API (`tak.saves.SaveFileManager`, `tak.saves.validateAgainstSchema`)
and lays down the slot shapes that API has to keep telling apart:

  slot_1   a valid run plus a second file beside it
  slot_2   a valid run whose day number the schema rejects (validation must keep saying so)
  slot_4   a primary file that is not JSON (damaged: listed, unpickable, never reused)
  slot_5   a primary file that is JSON but not an object (damaged the same way)
  slot_7   an empty primary file (damaged, not absent)
  slot_8   a directory with other files but no primary file (not a save)
  plus entries that are not slots at all: slot_0, slot_100, slot_x, a plain file named
  slot_6, notes.txt, and the schema directory

Modes: `write DIR`, `read DIR` (prints one JSON line), `append DIR`.
"""

import json
import os
import sys

from tak.saves import SaveFileManager, validateAgainstSchema

PRIMARY = "save.json"
SCHEMA_DIR = "_schema"
SCHEMA = {
    "type": "object",
    "required": ["day", "name"],
    "properties": {
        "day": {"type": "integer", "minimum": 1, "maximum": 365},
        "name": {"type": "string"},
        "money": {"type": "integer", "minimum": 0},
    },
}


def metadata(slot_path, data):
    return {"day": data.get("day"), "name": data.get("name")}


def manager(root):
    return SaveFileManager(data_directory=root, primaryFile=PRIMARY, readMetadata=metadata)


def write_slot(m, slot, files):
    m.select_save_slot(slot)
    for name, content in files.items():
        with open(m.get_save_path(name), "w") as f:
            f.write(content)


def write(root):
    os.makedirs(os.path.join(root, SCHEMA_DIR), exist_ok=True)
    with open(os.path.join(root, SCHEMA_DIR, "save.schema.json"), "w") as f:
        json.dump(SCHEMA, f)
    m = manager(root)
    write_slot(m, 1, {PRIMARY: json.dumps({"day": 3, "name": "Ada", "money": 12}),
                      "stats.json": json.dumps({"fish": [1, 2, 3]})})
    write_slot(m, 2, {PRIMARY: json.dumps({"day": 400, "name": "Grace", "money": 0})})
    write_slot(m, 4, {PRIMARY: "{not json"})
    write_slot(m, 5, {PRIMARY: "42"})
    write_slot(m, 7, {PRIMARY: ""})
    write_slot(m, 8, {"stats.json": "{}"})
    for name in ("slot_0", "slot_100", "slot_x"):
        os.makedirs(os.path.join(root, name), exist_ok=True)
        with open(os.path.join(root, name, PRIMARY), "w") as f:
            f.write(json.dumps({"day": 1, "name": name}))
    with open(os.path.join(root, "slot_6"), "w") as f:
        f.write("a file, not a slot")
    with open(os.path.join(root, "notes.txt"), "w") as f:
        f.write("not a slot either")


def validation(data, schema_path):
    try:
        validateAgainstSchema(data, schema_path)
        return "valid"
    except Exception as e:  # noqa: BLE001 — the exception class is part of the contract
        return f"invalid:{type(e).__name__}"


def read(root):
    m = manager(root)
    schema_path = os.path.join(root, SCHEMA_DIR, "save.schema.json")
    saves = []
    for s in m.list_save_files():
        entry = {"slot": s["slot"], "slot_name": s["slot_name"],
                 "path": os.path.relpath(s["path"], root), "metadata": s["metadata"]}
        if not s["metadata"].get("unreadable"):
            m.select_save_slot(s["slot"])
            with open(m.get_save_path(PRIMARY)) as f:
                data = json.load(f)
            entry["primary"] = data
            entry["validation"] = validation(data, schema_path)
        saves.append(entry)
    print(json.dumps({"saves": saves, "next_slot": m.get_next_available_slot()}, sort_keys=True))


def append(root):
    m = manager(root)
    slot = m.get_next_available_slot()
    if slot is None:
        raise SystemExit("no free slot")
    write_slot(m, slot, {PRIMARY: json.dumps({"day": 1, "name": "New", "money": 0})})


if __name__ == "__main__":
    mode, root = sys.argv[1], sys.argv[2]
    {"write": write, "read": read, "append": append}[mode](root)
