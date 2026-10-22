"""Save-compatibility scenario for trace-client-python (https://github.com/Stephenson-Software/trace-client-python).

trace-client keeps one piece of state on disk: the installation's ID file, which a program
names with `install_id_file=` and the client loads (or creates) through
`TraceClient.install_id_from_file`. The loader returns the first line that is an ID
(`[A-Za-z0-9_.-]`, 1 to 255 characters, surrounding whitespace ignored). A file that is
missing or holds no such line is REPLACED with a fresh uuid4 plus a newline; a file that exists
but cannot be read (not UTF-8, a directory) is KEPT, and a fresh ID is used in memory only.
This scenario lays down the shapes that loader has to keep telling apart, every one an entry
named `*.id` under DIR:

  created.id                 written by the client itself (a uuid4 and a newline)
  plain.id, trailing-newline.id, crlf.id, padded.id, tabs.id, uppercase-uuid.id,
  max-length.id (255 characters)
                             hand-written IDs the loader accepts as they are
  leading-blank-lines.id, first-of-two.id, invalid-lines-first.id, comment-first.id
                             the first ID line wins over blank, later or non-ID lines
  empty.id, whitespace-only.id, too-long.id (256), bad-chars.id, non-ascii.id, bom.id
                             no ID line: the loader replaces them
  not-utf8.id, id-then-not-utf8.id, a directory named directory.id
                             unreadable: kept, ID only in memory

`read DIR` must not change DIR, yet the loader rewrites files it replaces. So each entry is
first loaded from a copy in a temporary directory: an entry whose copy the loader rewrote is
reported as `replaced` (with what the rewrite looks like, never the random ID) and the
original is not touched; every other entry is then loaded in place, which is the read the
gate's files-kept assertions hold to "changes nothing". Each entry is also handed to a
DISABLED client (`install_id_file=`), which must load nothing and write nothing.

No network traffic: only the static loader and a client built with `enabled=False` (and
TRACE_USAGE_REPORTING=off) run, and a disabled client has no sender thread; `report` is
never called.

Modes: `write DIR`, `read DIR` (prints one JSON line), `append DIR` (the client creates one
new ID file, in a directory that does not exist yet).
"""

import json
import os
import re
import shutil
import sys
import tempfile

os.environ["TRACE_USAGE_REPORTING"] = "off"

from trace_client import TraceClient  # noqa: E402

UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

HAND_WRITTEN = {
    "plain.id": b"plain-id",
    "trailing-newline.id": b"with.newline_1\n",
    "crlf.id": b"crlf-id\r\n",
    "padded.id": b"   padded-id   \n",
    "tabs.id": b"\ttabbed_id\t\n",
    "uppercase-uuid.id": b"0F8FAD5B-D9CB-469F-A165-70867728950E\n",
    "max-length.id": b"a" * 255 + b"\n",
    "leading-blank-lines.id": b"\n\n   \n\t\nafter-blanks\n",
    "first-of-two.id": b"first-id\nsecond-id\n",
    "invalid-lines-first.id": b"has space\nnot/valid\n" + b"b" * 256 + b"\nthird.is-valid\n",
    "comment-first.id": b"# install id\nreal-id\n",
    "empty.id": b"",
    "whitespace-only.id": b"  \n\t\r\n",
    "too-long.id": b"c" * 256 + b"\n",
    "bad-chars.id": b"not an id!\n",
    "non-ascii.id": "café-id\n".encode("utf-8"),
    "bom.id": b"\xef\xbb\xbfbom-id\n",
    "not-utf8.id": b"\xff\xfe\x00not utf-8\n",
    "id-then-not-utf8.id": b"good-id\n\xff\xfe\n",
}


def write(root):
    os.makedirs(root, exist_ok=True)
    TraceClient.install_id_from_file(os.path.join(root, "created.id"))
    for name, content in HAND_WRITTEN.items():
        with open(os.path.join(root, name), "wb") as f:
            f.write(content)
    os.makedirs(os.path.join(root, "directory.id"))


def entries(root):
    found = []
    for here, dirs, files in os.walk(root):
        for name in dirs + files:
            if name.endswith(".id"):
                found.append(os.path.relpath(os.path.join(here, name), root))
        dirs[:] = [d for d in dirs if not d.endswith(".id")]
    return sorted(found)


def snapshot(path):
    if os.path.isdir(path):
        return ("dir", tuple(sorted(os.listdir(path))))
    with open(path, "rb") as f:
        return ("file", f.read())


def file_text(path):
    if os.path.isdir(path):
        return ""
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "replace")


def load(path):
    try:
        return TraceClient.install_id_from_file(path), None
    except Exception as e:  # noqa: BLE001 — the loader promises never to raise; the class is the evidence
        return None, type(e).__name__


def disabled_client(path):
    before = snapshot(path)
    client = TraceClient("http://disabled.invalid", "save-compat", "0", enabled=False, install_id_file=path)
    return {"install_id": client.install_id, "disabled_reason": client.disabled_reason,
            "enabled": client.enabled, "wrote": snapshot(path) != before}


def describe(root, rel):
    path = os.path.join(root, rel)
    entry = {"name": rel, "kind": "directory" if os.path.isdir(path) else "file"}
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "copy.id")
        (shutil.copytree if os.path.isdir(path) else shutil.copyfile)(path, copy)
        before = snapshot(copy)
        got, error = load(copy)
        after = snapshot(copy)
    if error:
        entry.update({"result": "error", "error": error})
    elif after != before:
        written = after[1].decode("utf-8", "replace") if after[0] == "file" else None
        entry.update({"result": "replaced", "new_id_is_uuid4": bool(UUID4.match(got or "")),
                      "file_is_new_id_and_newline": written == (got or "") + "\n"})
    else:
        got, error = load(path)
        if error:
            entry.update({"result": "error", "error": error})
        elif got and got in file_text(path):
            entry.update({"result": "loaded", "id": got})
        else:
            entry.update({"result": "in-memory", "id_is_uuid4": bool(UUID4.match(got or ""))})
        entry["disabled_client"] = disabled_client(path)
    return entry


def read(root):
    print(json.dumps({"saves": [describe(root, rel) for rel in entries(root)]}, sort_keys=True))


def append(root):
    path = os.path.join(root, "appended", "nested", "new.id")
    if os.path.exists(path):
        raise SystemExit("append target already exists")
    got = TraceClient.install_id_from_file(path)
    with open(path, encoding="utf-8") as f:
        if f.read() != got + "\n" or not UUID4.match(got):
            raise SystemExit("the client did not write a fresh uuid4 to the new ID file")


if __name__ == "__main__":
    mode, root = sys.argv[1], sys.argv[2]
    {"write": write, "read": read, "append": append}[mode](root)
