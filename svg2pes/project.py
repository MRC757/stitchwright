"""Project files (.svg2pes): everything needed to reopen a design and keep working.

A project is JSON. It stores each object's own outline (so the original SVG is
not needed), where it sits in the hoop, its threads, fill, satin and text
settings, groups, the sewing order and the window's settings.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from .fill import FillSpec, SatinSpec
from .svg_import import DesignObject, Polyline
from .text import TextSpec

FORMAT = "svg2pes-project"
VERSION = 1
EXTENSION = ".svg2pes"


@dataclass
class Project:
    objects: list[DesignObject]                   # in sewing-list order
    mats: dict = field(default_factory=dict)       # obj_id -> 6-number placement
    home: dict = field(default_factory=dict)
    colours: dict = field(default_factory=dict)    # obj_id -> rgb
    skipped: set = field(default_factory=set)
    fills: dict = field(default_factory=dict)      # obj_id -> FillSpec
    no_outline: set = field(default_factory=set)
    satins: dict = field(default_factory=dict)     # obj_id -> SatinSpec
    texts: dict = field(default_factory=dict)      # obj_id -> TextSpec
    groups: dict = field(default_factory=dict)     # obj_id -> group name
    color_order: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)   # hoop, margin, stitch length, ...
    source: str | None = None                      # the SVG it started from (for reference)
    warnings: list = field(default_factory=list)


def _r(v: float) -> float:
    return float(v)          # exact: JSON round-trips floats precisely


def _obj_to_dict(o: DesignObject) -> dict:
    return {"index": o.index, "id": o.obj_id, "kind": o.kind,
            "source_color": list(o.source_color), "stitches": o.stitches,
            "polylines": [{"closed": pl.closed, "points": [[_r(x), _r(y)] for x, y in pl.points]}
                          for pl in o.polylines]}


def _obj_from_dict(d: dict) -> DesignObject:
    polys = [Polyline([(float(x), float(y)) for x, y in pl["points"]], bool(pl["closed"]))
             for pl in d["polylines"]]
    rgb = tuple(int(c) for c in d["source_color"])
    return DesignObject(int(d["index"]), str(d["id"]), str(d["kind"]), rgb, polys, color=rgb,
                        stitches=bool(d.get("stitches", False)))


def to_dict(p: Project) -> dict:
    def spec(v):
        out = asdict(v)
        if out.get("color") is not None:
            out["color"] = list(out["color"])
        return out

    return {
        "format": FORMAT, "version": VERSION, "source": p.source,
        "settings": p.settings, "warnings": list(p.warnings),
        "objects": [_obj_to_dict(o) for o in p.objects],
        "mats": {k: [float(v) for v in m] for k, m in p.mats.items()},
        "home": {k: [float(v) for v in m] for k, m in p.home.items()},
        "colours": {k: list(v) for k, v in p.colours.items()},
        "skipped": sorted(p.skipped),
        "fills": {k: spec(v) for k, v in p.fills.items()},
        "no_outline": sorted(p.no_outline),
        "satins": {k: asdict(v) for k, v in p.satins.items()},
        "texts": {k: asdict(v) for k, v in p.texts.items()},
        "groups": dict(p.groups),
        "color_order": [[ph, list(rgb)] for ph, rgb in p.color_order],
    }


def from_dict(d: dict) -> Project:
    if d.get("format") != FORMAT:
        raise ValueError("This is not an svg2pes project file")
    if int(d.get("version", 0)) > VERSION:
        raise ValueError("This project was saved by a newer svg2pes; please update")

    def fill(v):
        v = dict(v)
        if v.get("color") is not None:
            v["color"] = tuple(v["color"])
        return FillSpec(**v)

    objects = [_obj_from_dict(o) for o in d.get("objects", [])]
    ids = {o.obj_id for o in objects}

    def known(m):
        return {k: v for k, v in m.items() if k in ids}

    return Project(
        objects=objects,
        mats={k: tuple(float(x) for x in v) for k, v in known(d.get("mats", {})).items()},
        home={k: tuple(float(x) for x in v) for k, v in known(d.get("home", {})).items()},
        colours={k: tuple(int(c) for c in v) for k, v in known(d.get("colours", {})).items()},
        skipped={k for k in d.get("skipped", []) if k in ids},
        fills={k: fill(v) for k, v in known(d.get("fills", {})).items()},
        no_outline={k for k in d.get("no_outline", []) if k in ids},
        satins={k: SatinSpec(**v) for k, v in known(d.get("satins", {})).items()},
        texts={k: TextSpec(**v) for k, v in known(d.get("texts", {})).items()},
        groups=known(d.get("groups", {})),
        color_order=[(int(ph), tuple(int(c) for c in rgb)) for ph, rgb in d.get("color_order", [])],
        settings=dict(d.get("settings", {})),
        source=d.get("source"),
        warnings=list(d.get("warnings", [])),
    )


def save_project(path: str, p: Project) -> None:
    """Write the project (to a temporary file first, so a failure can't corrupt it)."""
    tmp = path + ".part"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(to_dict(p), f, separators=(",", ":"))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def load_project(path: str) -> Project:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"{os.path.basename(path)} is not a readable project file ({e})") from e
    return from_dict(data)
