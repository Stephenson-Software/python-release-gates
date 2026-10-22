"""Save-compatibility scenario for usd-recorder (https://github.com/Preponderous-Software/usd-recorder).

usd-recorder's "saves" are the OpenUSD recordings it writes: `Recorder` stages (one Xform per
entity, sparse time samples with hold keys, typed extras, event channels, optionally chunked
into value-clip layers) and `usd-replay/1` replays (`ReplayWriter`: one Points prim per kind of
entity, colour-grid fields, static scene points, a state hash per sample). Its own `inspect`
and `replay` modules read them back, and downstream players and tools consume them, so a
recording an older release wrote must still read exactly the same. This scenario writes them
only through the public API (`usd_recorder.Recorder`, `flatten`, `usd_recorder.replay.ReplayWriter`):

  stage.usda       unchunked: int and string entity ids, a late arrival, an entity that leaves
                   and comes back, a colour given after the first tick, every typed value
                   (bool, int, double, string, double3) per entity and per stage, two event
                   channels (one given a list), gaps between ticks, a `scene` hook, metadata
  stage_y.usdc     binary, Y up, centimetres, sphere geometry, a custom root, scope and
                   namespaces, a per-id default colour and a custom entity naming
  chunked.usda     chunk_size=3: value clips over four chunk layers (plus topology and
                   manifest), an entity absent across a chunk boundary and one born in a later
                   chunk; flattened by `flatten` into chunked_flat.usda
  live.usda        checkpoint_every=2 (republished atomically while recording), then closed
  replay.usda      usd-replay/1 with every optional part: constant and per-point (colour,
                   width) sets, a cube set that empties, two fields (one held unchanged across
                   samples), scene points, stats, title/description/source/params
  replay.usdc      the same contract in binary, a single sample

Modes: `write DIR` (baseline), `read DIR` (prints one JSON line), `append DIR` (candidate: one
more unchunked recording beside the others).

`read` prints `{"saves": [...]}`, one entry per layer file in DIR (a chunked recording's
chunk, topology and manifest layers are read on their own as well as through the root),
each carrying `inspect_stage` (path made relative, `layers` sorted: USD returns a stage's used
layers in no fixed order) and `has_clips`. A Recorder stage adds what a USD reader resolves at
every integer tick from the start to the end time code (gaps included, through value clips):
each prim's type, each attribute's value, and each Imageable's computed visibility. A replay
(`customLayerData.usdReplay`) adds `validate_replay`'s problems, `replay_info` (path left out)
and everything `read_replay` loads (metadata, ticks, hashes, every set/field/stat/scene
array). Reading writes nothing, and the view carries no paths or times that vary by machine.
"""

import json
import math
import os
import sys

from pxr import Sdf, Usd, UsdGeom

from usd_recorder import Recorder, flatten, has_clips, inspect_stage
from usd_recorder.replay import ReplayWriter, read_replay, replay_info, validate_replay

LAYER_SUFFIXES = (".usda", ".usdc")


# -------------------------------------------------------------------------------------- write --


def ground(stage):
    cube = UsdGeom.Cube.Define(stage, "/World/Ground")
    cube.CreateSizeAttr().Set(10.0)
    cube.AddScaleOp().Set((1.0, 1.0, 0.01))


def write_stage(root):
    with Recorder(
        os.path.join(root, "stage.usda"),
        time_codes_per_second=12,
        scene=ground,
        metadata={"sim": "walkers", "seed": 7, "params": {"agents": 3, "wrap": True}},
    ) as rec:
        for tick in (0, 1, 2, 4, 5, 8):
            entities = {
                1: {"position": (tick, 0.0, 0.0), "energy": 10.0 - tick, "alive": True,
                    "age": tick, "state": "walking" if tick < 4 else "resting",
                    "heading": (1.0, 0.0, 0.0)},
            }
            if tick not in (2, 4):  # leaves and comes back
                entities["scout"] = {"position": (0.0, tick * 0.5, 0.0), "state": "looking"}
                if tick >= 1:
                    entities["scout"]["color"] = (0.1 * tick, 0.2, 0.9)
            if tick >= 4:  # a late arrival
                entities[-3] = {"position": (-1.0, -1.0, tick), "age": 0, "alive": tick < 8}
            events = {"log": ["born", "spotted"] if tick == 4 else ("moved" if tick == 1 else "")}
            if tick == 8:
                events["alarm"] = "dusk"
            rec.record(tick, entities, extras={"population": len(entities), "temperature": 20.5 - tick,
                                               "season": "spring", "night": tick >= 8,
                                               "wind": (0.0, 1.0, float(tick))}, events=events)


def write_stage_y(root):
    with Recorder(
        os.path.join(root, "stage_y.usdc"),
        time_codes_per_second=30,
        binary=True,
        root="/Sim",
        entities="Agents",
        up_axis="Y",
        meters_per_unit=0.01,
        geometry="sphere",
        entity_size=0.5,
        default_color=lambda entity_id: (0.0, 0.5, 1.0) if entity_id % 2 else (1.0, 0.5, 0.0),
        entity_name=lambda entity_id: f"agent_{entity_id:03d}",
        namespace="sim:vals",
        events_namespace="sim:events",
        metadata={"run": "y-up"},
    ) as rec:
        for tick in range(4):
            rec.record(tick, {n: {"position": (n, tick, 0.0), "hp": 5 - tick + n} for n in range(2 + tick % 2)},
                       extras={"tick_label": f"t{tick}"}, events={"note": "odd" if tick % 2 else ""})


def write_chunked(root):
    path = os.path.join(root, "chunked.usda")
    with Recorder(path, time_codes_per_second=24, chunk_size=3, metadata={"sim": "chunks"}) as rec:
        for tick in range(10):
            entities = {7: {"position": (tick, tick, 0.0), "speed": 0.25 * tick}}
            if not 2 <= tick <= 4:  # absent across the boundary between chunks 0 and 1
                entities[8] = {"position": (0.0, 0.0, tick), "mood": "calm" if tick < 6 else "busy"}
            if tick >= 7:  # born in the third chunk
                entities[9] = {"position": (9.0, 9.0, 9.0), "color": (1.0, 0.0, 0.0)}
            rec.record(tick, entities, extras={"count": len(entities)},
                       events={"log": f"tick {tick}" if tick % 3 == 0 else ""})
    flatten(path, os.path.join(root, "chunked_flat.usda"))


def write_live(root):
    with Recorder(os.path.join(root, "live.usda"), checkpoint_every=2, geometry=None) as rec:
        for tick in range(5):
            rec.record(tick, {0: {"position": (0.0, 0.0, tick), "flag": tick % 2 == 0}},
                       extras={"progress": tick / 4})


def write_replays(root):
    with ReplayWriter(
        os.path.join(root, "replay.usda"),
        sim="grazers",
        sim_version="1.2.0+abc1234",
        seed=18446744073709551615,
        tick_rate=10,
        state_hash_algorithm="grazers fingerprint (FNV-1a 64)",
        playback_rate=20,
        meters_per_unit=0.5,
        title="Grazers",
        description="Grass, grazers and one fox.",
        source="https://example.invalid/grazers",
        params={"grazers": 3, "regrow": 0.25},
    ) as w:
        w.entity_set("grazer", label="Grazers", color="#5a9e4b", width=0.75)
        w.entity_set("fox", color=None, width=None, shape="sphere")
        w.entity_set("rock", label="Falling rocks", color=(200, 40, 40), width=2.0, shape="cube")
        w.field("grass", label="Grass", cols=3, rows=2, cell_size=2.0, origin=(-3.0, -2.0), z=-0.1)
        w.field("water", cols=1, rows=1)
        w.scene_points("trees", [(1.0, 1.0, 0.0), (2.0, -1.0, 0.0)], widths=[1.5, 2.5],
                       color=["#2e5d34", "#3a7a40"], label="Trees", shape="cube")
        w.scene_points("well", [(0.0, 0.0, 0.0)])
        for i, tick in enumerate((0, 4, 8, 20)):
            w.sample(
                tick,
                state_hash=f"{(tick + 1) * 0x9E3779B97F4A7C15 % 2**64:016x}",
                entities={
                    "grazer": {"ids": list(range(3 - i % 2)), "points": [(n, i, 0.0) for n in range(3 - i % 2)]},
                    "fox": {"ids": [42], "points": [(i * 0.5, -i, 0.0)], "colors": [(0.9, 0.4 + 0.1 * i, 0.1)],
                            "widths": [1.0 + i]},
                    "rock": {"ids": [5] if i < 2 else [], "points": [(0.0, 0.0, 9.0 - i)] if i < 2 else []},
                },
                fields={"grass": [(0, 128 + 20 * i, 0)] * 6, "water": ["#1f4e8c"]} if i != 2 else {},
                stats={"population": 4 - i % 2, "grass_cover": 1.0 - 0.125 * i},
            )
    with ReplayWriter(os.path.join(root, "replay.usdc"), sim="tiny", sim_version="0.1", seed=0,
                      tick_rate=1, state_hash_algorithm="none") as w:
        w.entity_set("dot")
        w.sample(3, state_hash="0123abcd", entities={"dot": {"ids": [1, 2], "points": [(0, 0, 0), (1, 1, 1)]}})


def write(root):
    os.makedirs(root, exist_ok=True)
    write_stage(root)
    write_stage_y(root)
    write_chunked(root)
    write_live(root)
    write_replays(root)


# --------------------------------------------------------------------------------------- read --


def plain(value):
    """A USD/numpy value as JSON: tokens and asset paths as strings, vectors and arrays as lists."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if hasattr(value, "tolist"):  # numpy arrays and scalars
        return plain(value.tolist())
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in sorted(value.items())}
    try:
        return [plain(v) for v in value]
    except TypeError:
        return str(value)


def frames(path):
    """Every prim's type and attribute values, resolved at each integer tick of the stage."""
    stage = Usd.Stage.Open(path)
    start, end = stage.GetStartTimeCode(), stage.GetEndTimeCode()
    ticks = list(range(math.floor(start), math.ceil(end) + 1)) if stage.HasAuthoredTimeCodeRange() else []
    prims = {}
    for prim in stage.TraverseAll():
        attrs = {}
        for attr in prim.GetAttributes():
            if not attr.HasAuthoredValue() and not attr.GetNumTimeSamples():
                continue
            if attr.GetNumTimeSamples():
                attrs[attr.GetName()] = {str(t): plain(attr.Get(Usd.TimeCode(t))) for t in ticks}
            else:
                attrs[attr.GetName()] = plain(attr.Get())
        entry = {"type": str(prim.GetTypeName()), "attributes": attrs}
        if prim.IsA(UsdGeom.Imageable):
            img = UsdGeom.Imageable(prim)
            entry["visibility"] = {str(t): str(img.ComputeVisibility(Usd.TimeCode(t))) for t in ticks}
        prims[str(prim.GetPath())] = entry
    return {"ticks": ticks, "prims": prims}


def replay_view(path):
    r = read_replay(path)
    info = replay_info(path)
    info.pop("path")
    return {
        "problems": validate_replay(path),
        "info": plain(info),
        "meta": plain(r.meta),
        "playbackRate": r.playback_rate,
        "metersPerUnit": r.meters_per_unit,
        "ticks": r.ticks,
        "hashes": r.hashes,
        "sets": [{"name": s.name, "label": s.label, "shape": s.shape, "ids": plain(s.ids),
                  "points": plain(s.points), "colors": plain(s.colors), "widths": plain(s.widths),
                  "vertexColor": s.vertex_color, "vertexWidth": s.vertex_width} for s in r.sets],
        "fields": [{"name": f.name, "label": f.label, "size": [f.cols, f.rows], "origin": list(f.origin),
                    "cellSize": f.cell_size, "z": f.z, "ticks": f.ticks, "colors": plain(f.colors)}
                   for f in r.fields],
        "stats": {k: [ts, vs] for k, (ts, vs) in sorted(r.stats.items())},
        "scene": [{"name": s.name, "label": s.label, "shape": s.shape, "points": plain(s.points),
                   "colors": plain(s.colors), "widths": plain(s.widths)} for s in r.scene],
    }


def read(root):
    saves = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not (os.path.isfile(path) and name.endswith(LAYER_SUFFIXES)):
            continue
        summary = inspect_stage(path)
        summary["path"] = name
        summary["layers"] = sorted(summary["layers"])  # a stage's used layers are a set: no order
        entry = {"file": name, "inspect": plain(summary), "hasClips": has_clips(Sdf.Layer.FindOrOpen(path))}
        if "usdReplay" in summary["customLayerData"]:
            entry["replay"] = replay_view(path)
        else:
            entry["frames"] = frames(path)
        saves.append(entry)
    print(json.dumps({"saves": saves}, sort_keys=True))


# ------------------------------------------------------------------------------------- append --


def append(root):
    n = 1
    while os.path.exists(os.path.join(root, f"appended_{n}.usda")):
        n += 1
    with Recorder(os.path.join(root, f"appended_{n}.usda"), metadata={"sim": "appended"}) as rec:
        for tick in range(3):
            rec.record(tick, {1: {"position": (tick, 0.0, 0.0), "energy": 1.0 + tick}},
                       extras={"count": 1}, events={"log": "start" if tick == 0 else ""})


if __name__ == "__main__":
    mode, root = sys.argv[1], sys.argv[2]
    {"write": write, "read": read, "append": append}[mode](root)
