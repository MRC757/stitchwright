"""The design being edited, with every operation on it, and no user interface.

A Document holds each object's own outline (in the coordinates it was loaded in)
plus a placement matrix that puts it in the hoop (millimetres, hoop centre at
0,0, y down), and the operator's choices for it: thread, fill, satin, skip, group.
Moving, resizing and rotating only change placements, so the artwork never
degrades, and every change can be undone.

The window (gui.py) turns clicks into calls on a Document and redraws; tests drive
a Document directly. Methods that can't do what was asked raise DocError with a
message meant for the user.
"""
from __future__ import annotations

import copy as _copy
import math
from dataclasses import dataclass, field, replace

from .convert import ConversionError, Plan, outside_hoop, plan_design
from .fill import FillSpec, SatinSpec, can_fill
from .geometry import IDENTITY, Affine, apply, bounds, chain, rotate, scale, translate
from .palette import nearest_brother
from .project import Project
from .stitching import Settings
from .svg_import import Design, DesignObject, Polyline
from .text import TextSpec, text_polylines

RGB = tuple[int, int, int]
UNDO_LIMIT = 100


class DocError(ValueError):
    """An operation that can't be done; the message is meant for the user."""


@dataclass
class PlanResult:
    """What planning the document produced, for the window to show."""
    plan: Plan | None
    error: str = ""                                   # why there is no plan
    placed: dict = field(default_factory=dict)        # obj_id -> placed point lists (mm)
    outside: list = field(default_factory=list)       # objects reaching outside the area
    outside_stitches_only: list = field(default_factory=list)   # ... only by their stitches


class Document:
    def __init__(self):
        self.base_objs: dict[str, DesignObject] = {}   # objects from the opened file
        self.text_objs: dict[str, DesignObject] = {}   # text objects (their settings: texts)
        self.extra_objs: dict[str, DesignObject] = {}  # copies, imports, traced pictures
        self.texts: dict[str, TextSpec] = {}
        self.order_ids: list[str] = []                 # the objects, in list / paint order
        self.mats: dict[str, Affine] = {}              # outline -> hoop placement
        self.home: dict[str, Affine] = {}              # placement when opened / added
        self.colours: dict[str, RGB] = {}              # chosen thread rgb
        self.skipped: set[str] = set()
        self.fills: dict[str, FillSpec] = {}
        self.no_outline: set[str] = set()
        self.satins: dict[str, SatinSpec] = {}
        self.groups: dict[str, str] = {}               # obj_id -> group name
        self.color_order: list = []                    # user's sewing order [(phase, rgb)]
        self.warnings: list[str] = []
        self.svg_path: str | None = None               # the file the design started from
        self.project_path: str | None = None
        self.dirty = False                             # changes since last saved
        self.hoop: tuple[float, float] = (100.0, 100.0)
        self.margin = 2.0
        self.clipboard: list[dict] = []
        self.last_text: TextSpec | None = None
        self.undo_stack: list = []
        self.redo_stack: list = []

    # ------------------------------------------------------------------ objects
    def obj(self, obj_id: str) -> DesignObject:
        return (self.text_objs.get(obj_id) or self.extra_objs.get(obj_id)
                or self.base_objs[obj_id])

    def chosen_rgb(self, o: DesignObject) -> RGB:
        """The thread an object is sewn in: the operator's pick, or the nearest thread."""
        return self.colours.get(o.obj_id) or nearest_brother(o.source_color).rgb

    def _taken(self, oid: str) -> bool:
        return oid in self.mats or oid in self.base_objs or oid in self.text_objs

    def _next_index(self) -> int:
        return max([self.obj(i).index for i in self.order_ids], default=0) + 1

    def placed_points(self, oid: str) -> list[list[tuple[float, float]]]:
        return [apply(self.mats[oid], pl.points) for pl in self.obj(oid).polylines]

    def bounds(self, ids) -> tuple[float, float, float, float] | None:
        """Bounding box of the objects as placed in the hoop (mm), or None."""
        pts = [p for i in ids for pl in self.placed_points(i) for p in pl]
        return bounds(pts) if pts else None

    def area(self) -> tuple[float, float, float, float]:
        """The sewing area: the hoop minus its margin, centred on (0, 0)."""
        hw, hh = self.hoop[0] / 2 - self.margin, self.hoop[1] / 2 - self.margin
        return (-hw, -hh, hw, hh)

    # ------------------------------------------------------------------ undo
    def _state(self):
        return (dict(self.mats), dict(self.home), dict(self.colours), set(self.skipped),
                dict(self.texts), dict(self.text_objs), list(self.order_ids),
                dict(self.fills), set(self.no_outline), dict(self.satins), list(self.color_order),
                dict(self.extra_objs), dict(self.groups))

    def _restore(self, st):
        (self.mats, self.home, self.colours, self.skipped, self.texts, self.text_objs,
         self.order_ids, self.fills, self.no_outline, self.satins, self.color_order,
         self.extra_objs, self.groups) = st

    def push_undo(self):
        """Record the current state; call before every change."""
        self.dirty = True
        self.undo_stack.append(self._state())
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append(self._state())
        self._restore(self.undo_stack.pop())
        self.dirty = True
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append(self._state())
        self._restore(self.redo_stack.pop())
        self.dirty = True
        return True

    # ------------------------------------------------------------------ file status
    def set_hoop(self, hoop: tuple[float, float], margin: float):
        if hoop[0] <= 0 or hoop[1] <= 0:
            raise DocError("Hoop size must be more than 0.")
        self.hoop, self.margin = (float(hoop[0]), float(hoop[1])), float(margin)

    def note_source(self, path: str):
        """Remember the file artwork came from, if the design has none yet (it names
        the saved files)."""
        if self.svg_path is None:
            self.svg_path = path

    def mark_changed(self):
        """A change that isn't an undoable edit (e.g. a setting saved in the project)."""
        if self.order_ids:
            self.dirty = True

    def mark_saved(self, project_path: str | None = None):
        if project_path:
            self.project_path = project_path
        self.dirty = False

    # ------------------------------------------------------------------ loading
    def clear(self):
        """An empty design (keeps the hoop size and the clipboard)."""
        for d in (self.base_objs, self.text_objs, self.extra_objs, self.groups, self.texts,
                  self.colours, self.fills, self.satins, self.mats, self.home):
            d.clear()
        self.no_outline.clear()
        self.skipped.clear()
        self.color_order = []
        self.order_ids = []
        self.warnings = []
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.svg_path = None
        self.project_path = None
        self.dirty = False

    def fit_matrix(self, objects) -> tuple[Affine, float]:
        """Placement that centres objects in the hoop, shrunk to fit if too big;
        returns (matrix, scale)."""
        x0, y0, x1, y1 = bounds([p for o in objects for pl in o.polylines for p in pl.points])
        aw, ah = self.hoop[0] - 2 * self.margin, self.hoop[1] - 2 * self.margin
        limits = [a / d for a, d in ((aw, x1 - x0), (ah, y1 - y0)) if d > 0]
        s = min([1.0] + limits)
        return chain(translate(-(x0 + x1) / 2, -(y0 + y1) / 2), scale(s)), s

    def load_design(self, design: Design, path: str) -> float:
        """Start over with a loaded file's objects, centred in the hoop (embroidery
        files stay where they are). Returns the scale used to fit the hoop."""
        self.clear()
        self.svg_path = path
        for o in design.objects:
            (self.text_objs if o.obj_id in design.texts else self.base_objs)[o.obj_id] = o
        self.texts.update(design.texts)
        self.order_ids = [o.obj_id for o in design.objects]
        self.warnings = list(design.warnings)
        if any(o.stitches for o in design.objects):
            m, s = IDENTITY, 1.0
        else:
            m, s = self.fit_matrix(design.objects)
        self.mats = {i: m for i in self.order_ids}
        self.home = dict(self.mats)
        return s

    @staticmethod
    def scale_objects(design: Design, k: float) -> None:
        """Scale a loaded design's outlines (e.g. 96/72 for Illustrator exports)."""
        for o in design.objects:
            o.polylines = [Polyline([(x * k, y * k) for x, y in pl.points], pl.closed)
                           for pl in o.polylines]

    def add_objects(self, objects, keep_place: bool = False, fills=None, texts=None) -> list[str]:
        """Add objects (renamed if their ids are taken), centred in the hoop or left
        where they are. Returns their ids."""
        self.push_undo()
        index = self._next_index()
        m = IDENTITY if keep_place else self.fit_matrix(objects)[0]
        new_ids = []
        for o in objects:
            nid, n = o.obj_id, 2
            while self._taken(nid):
                nid = f"{o.obj_id}_{n}"
                n += 1
            if texts and o.obj_id in texts:
                self.text_objs[nid] = replace(o, index=index, obj_id=nid)
                self.texts[nid] = texts[o.obj_id]
            else:
                self.extra_objs[nid] = replace(o, index=index, obj_id=nid)
            index += 1
            self.order_ids.append(nid)
            self.mats[nid] = self.home[nid] = m
            if fills and o.obj_id in fills:
                self.fills[nid] = fills[o.obj_id]
            new_ids.append(nid)
        return new_ids

    def add_traced(self, objects, fill: bool) -> list[str]:
        """Add a traced picture's objects; filled (without outlines) if asked."""
        ids = self.add_objects(objects, fills={o.obj_id: FillSpec() for o in objects} if fill else None)
        if fill:
            self.no_outline.update(ids)
        return ids

    # ------------------------------------------------------------------ threads and stitches
    def set_thread(self, ids, rgb: RGB | None):
        """Sew these objects in the thread rgb (None: back to the nearest match)."""
        self.push_undo()
        for i in ids:
            if rgb is None:
                self.colours.pop(i, None)
            else:
                self.colours[i] = rgb

    def same_source_colour(self, oid: str) -> list[str]:
        """Other objects that had the same colour in the artwork."""
        c = self.obj(oid).source_color
        return [i for i in self.order_ids if i != oid and self.obj(i).source_color == c]

    def toggle_skip(self, ids):
        """Skip the objects, or sew them again if all were already skipped."""
        self.push_undo()
        skip = any(i not in self.skipped for i in ids)
        for i in ids:
            (self.skipped.add if skip else self.skipped.discard)(i)

    def stitch_targets(self, ids) -> tuple[list[str], list[str]]:
        """(objects whose fill/outline can be set, those of them that can be filled).
        Raises DocError if none can (imported stitches are sewn as they are)."""
        ids = [i for i in ids if not self.obj(i).stitches]
        if not ids:
            raise DocError("Imported stitches are sewn as they are; only their thread can change.")
        return ids, [i for i in ids if can_fill(self.obj(i).polylines)]

    def set_stitches(self, ids, fill: FillSpec | None, outline: bool,
                     satin: SatinSpec | None) -> list[str]:
        """Set fill, outline on/off and satin for the objects. Returns the ids that
        couldn't be filled (open shapes) when a fill was asked for."""
        ids, fillable = self.stitch_targets(ids)
        self.push_undo()
        for i in ids:
            if i in fillable:
                if fill is None:
                    self.fills.pop(i, None)
                else:
                    self.fills[i] = fill
            (self.no_outline.discard if outline else self.no_outline.add)(i)
            if satin is None:
                self.satins.pop(i, None)
            else:
                self.satins[i] = satin
        return [i for i in ids if i not in fillable] if fill is not None else []

    def placed_fill(self, oid: str) -> FillSpec | None:
        """The object's fill with its row angle turned by the object's own rotation,
        so the rows keep their direction relative to the shape."""
        spec = self.fills.get(oid)
        if spec is None:
            return None
        a, b, c, d, _, _ = self.mats[oid]
        turn = math.degrees(math.atan2(b, a))
        angle = spec.angle + turn if a * d - b * c >= 0 else turn - spec.angle   # mirrored
        return replace(spec, angle=angle)

    def reset_all(self):
        """Back to the artwork's own threads and positions (text and copies stay)."""
        if not self.order_ids:
            return
        self.push_undo()
        for d in (self.colours, self.fills, self.satins):
            d.clear()
        self.skipped.clear()
        self.no_outline.clear()
        self.color_order = []
        self.mats = dict(self.home)

    # ------------------------------------------------------------------ delete, copy, paste
    def _forget(self, i: str):
        for d in (self.texts, self.text_objs, self.extra_objs, self.mats, self.home,
                  self.colours, self.fills, self.satins, self.groups):
            d.pop(i, None)
        self.skipped.discard(i)
        self.no_outline.discard(i)
        if i in self.order_ids:
            self.order_ids.remove(i)

    def delete(self, ids):
        self.push_undo()
        for i in list(ids):
            self._forget(i)

    def _snapshot(self, i: str) -> dict:
        return {"obj": self.obj(i), "mat": self.mats[i], "colour": self.colours.get(i),
                "skipped": i in self.skipped, "fill": self.fills.get(i),
                "no_outline": i in self.no_outline, "satin": self.satins.get(i),
                "text": self.texts.get(i), "group": self.groups.get(i)}

    def _copy_id(self, base: str) -> str:
        base = base.split("_copy")[0]
        n = 1
        while self._taken(f"{base}_copy{n}"):
            n += 1
        return f"{base}_copy{n}"

    def copy(self, ids):
        self.clipboard = [self._snapshot(i) for i in ids]

    def paste(self, offset: float = 5.0) -> list[str]:
        """Add copies of the clipboard, offset down and right. Returns their ids."""
        if not self.clipboard:
            raise DocError("Nothing to paste: copy objects first (Ctrl+C).")
        self.push_undo()
        index = self._next_index()
        new_groups: dict[str, str] = {}
        new_ids = []
        for snap in self.clipboard:
            o = snap["obj"]
            nid = self._copy_id(o.obj_id)
            dup = replace(o, index=index, obj_id=nid)
            index += 1
            if snap["text"] is not None:
                self.text_objs[nid] = dup
                self.texts[nid] = snap["text"]
            else:
                self.extra_objs[nid] = dup
            self.order_ids.append(nid)
            self.mats[nid] = self.home[nid] = chain(snap["mat"], translate(offset, offset))
            for key, store in (("colour", self.colours), ("fill", self.fills),
                               ("satin", self.satins)):
                if snap[key] is not None:
                    store[nid] = snap[key]
            if snap["skipped"]:
                self.skipped.add(nid)
            if snap["no_outline"]:
                self.no_outline.add(nid)
            if snap["group"]:
                if snap["group"] not in new_groups:          # copies form a group of their own
                    new_groups[snap["group"]] = self._new_group_name()
                self.groups[nid] = new_groups[snap["group"]]
            new_ids.append(nid)
        return new_ids

    def duplicate(self, ids) -> list[str]:
        saved = self.clipboard
        try:
            self.copy(ids)
            return self.paste()
        finally:
            self.clipboard = saved

    # ------------------------------------------------------------------ groups
    def _new_group_name(self) -> str:
        n = 1
        while f"group {n}" in self.groups.values():
            n += 1
        return f"group {n}"

    def expand_groups(self, ids) -> list[str]:
        """ids plus every other member of their groups."""
        gids = {self.groups[i] for i in ids if i in self.groups}
        extra = [i for i in self.order_ids if self.groups.get(i) in gids and i not in ids]
        return list(ids) + extra

    def group(self, ids) -> str:
        if len(ids) < 2:
            raise DocError("Select two or more objects to group them.")
        self.push_undo()
        gid = self._new_group_name()
        for i in ids:
            self.groups[i] = gid
        return gid

    def ungroup(self, ids) -> int:
        ids = [i for i in ids if i in self.groups]
        if ids:
            self.push_undo()
            for i in ids:
                del self.groups[i]
        return len(ids)

    def units(self, ids) -> list[list[str]]:
        """Objects that move together: each group is one unit, other objects on their own."""
        units, seen = [], {}
        for i in ids:
            g = self.groups.get(i)
            if g is None:
                units.append([i])
            elif g in seen:
                seen[g].append(i)
            else:
                seen[g] = [i]
                units.append(seen[g])
        return units

    # ------------------------------------------------------------------ arranging
    def transform(self, ids, m: Affine):
        """Apply a placement change to the objects (in hoop coordinates)."""
        ids = list(ids)
        if not ids:
            return
        self.push_undo()
        for i in ids:
            self.mats[i] = chain(self.mats[i], m)

    def _centre(self, ids):
        b = self.bounds(ids)
        if b is None:
            raise DocError("Nothing to arrange.")
        return b, ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)

    def flip(self, ids, horizontal: bool):
        _, (cx, cy) = self._centre(ids)
        self.transform(ids, scale(-1, 1, cx, cy) if horizontal else scale(1, -1, cx, cy))

    def rotate(self, ids, degrees: float):
        _, (cx, cy) = self._centre(ids)
        self.transform(ids, rotate(degrees, cx, cy))

    def move_to(self, ids, x: float, y: float):
        """Move so the objects' centre is at (x, y) (mm from the hoop centre)."""
        _, (cx, cy) = self._centre(ids)
        self.transform(ids, translate(x - cx, y - cy))

    def centre(self, ids):
        self.move_to(ids, 0.0, 0.0)

    def resize(self, ids, width: float, height: float, keep_ratio: bool = True,
               width_changed: bool = True):
        """Resize about the centre to width x height (mm). With keep_ratio, the side the
        user changed (width_changed) sets the scale for both."""
        if width <= 0 or height <= 0:
            raise DocError("Size must be more than 0.")
        b, (cx, cy) = self._centre(ids)
        cw, ch = b[2] - b[0], b[3] - b[1]
        sx = width / cw if cw > 1e-6 else 1.0
        sy = height / ch if ch > 1e-6 else 1.0
        if keep_ratio:
            if width_changed or ch <= 1e-6:
                sy = sx
            else:
                sx = sy
        self.transform(ids, scale(sx, sy, cx, cy))

    def fit(self, ids, reach: dict | None = None):
        """Scale up or down and centre so the objects fill the sewing area. reach maps
        ids to their stitch extents (satin and fill edges go past the outlines), so
        those still fit."""
        b, (cx, cy) = self._centre(ids)
        x0, y0, x1, y1 = b
        aw, ah = self.hoop[0] - 2 * self.margin, self.hoop[1] - 2 * self.margin
        rs = [r for r in ((reach or {}).get(i) for i in ids) if r]
        if rs:
            ex = max(x0 - min(r[0] for r in rs), 0) + max(max(r[2] for r in rs) - x1, 0)
            ey = max(y0 - min(r[1] for r in rs), 0) + max(max(r[3] for r in rs) - y1, 0)
            aw, ah = aw - ex - 0.1, ah - ey - 0.1
        limits = [a / d for a, d in ((aw, x1 - x0), (ah, y1 - y0)) if d > 1e-6 and a > 0]
        if not limits:
            raise DocError("These objects have no size to fit.")
        self.transform(ids, chain(translate(-cx, -cy), scale(min(limits))))

    def reset(self, ids):
        """Back to where the objects were when opened or added."""
        ids = list(ids)
        if ids:
            self.push_undo()
            for i in ids:
                self.mats[i] = self.home[i]

    def align(self, ids, how: str):
        """Line up edges or centres (how: left, hcentre, right, top, vcentre, bottom).
        One object or group is aligned to the sewing area; groups move as one."""
        units = self.units(ids)
        if not units:
            raise DocError("Select objects to align.")
        ref = self.area() if len(units) == 1 else self.bounds(ids)
        self.push_undo()
        for u in units:
            b = self.bounds(u)
            dx = {"left": ref[0] - b[0], "right": ref[2] - b[2],
                  "hcentre": (ref[0] + ref[2]) / 2 - (b[0] + b[2]) / 2}.get(how, 0.0)
            dy = {"top": ref[1] - b[1], "bottom": ref[3] - b[3],
                  "vcentre": (ref[1] + ref[3]) / 2 - (b[1] + b[3]) / 2}.get(how, 0.0)
            for i in u:
                self.mats[i] = chain(self.mats[i], translate(dx, dy))

    def distribute(self, ids, horizontal: bool):
        """Space three or more objects (or groups) evenly, keeping the outer two."""
        units = self.units(ids)
        if len(units) < 3:
            raise DocError("Select three or more objects (or groups) to space them evenly.")
        k = 0 if horizontal else 1
        boxes = sorted(((self.bounds(u), u) for u in units), key=lambda bu: bu[0][k])
        first, last = boxes[0][0], boxes[-1][0]
        total = sum(b[k + 2] - b[k] for b, _ in boxes)
        gap = ((last[k + 2] - first[k]) - total) / (len(boxes) - 1)
        self.push_undo()
        pos = first[k]
        for b, u in boxes:
            shift = pos - b[k]
            for i in u:
                self.mats[i] = chain(self.mats[i], translate(shift, 0) if horizontal
                                     else translate(0, shift))
            pos += (b[k + 2] - b[k]) + gap

    # ------------------------------------------------------------------ text
    @staticmethod
    def _letters(spec: TextSpec):
        try:
            polys, missing = text_polylines(spec)
        except ValueError as e:                  # e.g. the font file can't be read
            raise DocError(str(e)) from e
        if not polys:
            raise DocError("That text has no letters this font can draw.")
        return polys, missing

    def add_text(self, spec: TextSpec) -> tuple[str, str]:
        """Add text in the middle of the hoop, in 900 Black. Returns (id, characters
        the font couldn't draw)."""
        polys, missing = self._letters(spec)
        self.push_undo()
        n = 1
        while self._taken(f"text{n}"):
            n += 1
        oid = f"text{n}"
        self.text_objs[oid] = DesignObject(self._next_index(), oid, "Text", (0, 0, 0), polys,
                                           color=(0, 0, 0))
        self.texts[oid] = spec
        self.order_ids.append(oid)
        self.mats[oid] = self.home[oid] = IDENTITY
        self.last_text = spec
        return oid, missing

    def edit_text(self, oid: str, spec: TextSpec) -> str:
        """Change a text object's words or style; it stays where it is. Returns the
        characters the font couldn't draw."""
        polys, missing = self._letters(spec)
        self.push_undo()
        o = self.text_objs[oid]
        old = [q for pl in o.polylines for q in pl.points]
        if old:                       # SVG text isn't centred on its own origin: keep its centre
            ob = bounds(old)
            cx, cy = (ob[0] + ob[2]) / 2, (ob[1] + ob[3]) / 2
            polys = [Polyline([(x + cx, y + cy) for x, y in pl.points], pl.closed) for pl in polys]
        self.text_objs[oid] = DesignObject(o.index, oid, "Text", o.source_color, polys,
                                           color=o.source_color, fill_rule=o.fill_rule)
        self.texts[oid] = spec
        self.last_text = spec
        return missing

    # ------------------------------------------------------------------ sewing order
    @staticmethod
    def stop_keys(tu) -> list:
        """(phase, rgb) keys of the parts sewn in one colour stop (fills, then outlines)."""
        phases = sorted({0 if lab.endswith(" fill") else 1 for lab in tu.object_ids})
        return [(ph, tuple(tu.rgb)) for ph in phases]

    def move_stop(self, thread_map, i: int, step: int):
        """Move colour stop i of the plan's thread_map earlier (-1) or later (+1).
        Fills always stay before outlines: a stop holding a fill and an outline moves
        its fill among the fills."""
        j = i + step
        if not 0 <= j < len(thread_map):
            return
        keys = [self.stop_keys(tu) for tu in thread_map]
        before = [k for ks in keys for k in ks]
        keys[i], keys[j] = keys[j], keys[i]
        order = sorted((k for ks in keys for k in ks), key=lambda k: k[0])
        if order == before:
            raise DocError("Fills are always sewn before outlines, so this stop can't move "
                           "past that one.")
        self.push_undo()
        self.color_order = order
        return keys[j][0]

    def auto_order(self):
        if self.color_order:
            self.push_undo()
            self.color_order = []

    # ------------------------------------------------------------------ planning
    def build_design(self) -> tuple[Design, dict]:
        """The objects as placed in the hoop, with their choices applied, ready to plan;
        and each object's placed point lists."""
        objs, placed = [], {}
        for oid in self.order_ids:
            o = self.obj(oid)
            polys = [Polyline(apply(self.mats[oid], pl.points), pl.closed) for pl in o.polylines]
            placed[oid] = [pl.points for pl in polys]
            objs.append(DesignObject(o.index, oid, o.kind, o.source_color, polys,
                                     color=self.colours.get(oid, o.source_color),
                                     skipped=oid in self.skipped,
                                     fill=self.placed_fill(oid),
                                     outline=oid not in self.no_outline,
                                     satin=self.satins.get(oid),
                                     stitches=o.stitches, fill_rule=o.fill_rule))
        return Design(objs, list(self.warnings)), placed

    def plan(self, settings: Settings) -> PlanResult:
        """Plan the stitches and check them against the sewing area."""
        design, placed = self.build_design()
        if not self.order_ids:
            return PlanResult(None, "", placed)
        try:
            plan = plan_design(design, hoop_mm=self.hoop, margin_mm=self.margin,
                               settings=replace(settings, color_order=tuple(self.color_order)),
                               placed=True)
        except ConversionError as e:
            return PlanResult(None, str(e), placed)
        outside = outside_hoop(design, self.hoop, self.margin, plan.report)
        by_outline = outside_hoop(design, self.hoop, self.margin)
        return PlanResult(plan, "", placed, outside, [i for i in outside if i not in by_outline])

    # ------------------------------------------------------------------ projects
    def to_project(self, settings: dict) -> Project:
        return Project([self.obj(i) for i in self.order_ids], dict(self.mats), dict(self.home),
                       dict(self.colours), set(self.skipped), dict(self.fills),
                       set(self.no_outline), dict(self.satins), dict(self.texts),
                       dict(self.groups), list(self.color_order), dict(settings),
                       self.svg_path, list(self.warnings))

    def load_project(self, proj: Project, path: str) -> dict:
        """Replace the design with a project's. Returns the project's settings (hoop,
        stitch length, ...) for the caller to apply."""
        self.clear()
        for o in proj.objects:
            (self.text_objs if o.obj_id in proj.texts else self.base_objs)[o.obj_id] = o
        self.order_ids = [o.obj_id for o in proj.objects]
        self.mats = {i: proj.mats.get(i, IDENTITY) for i in self.order_ids}
        self.home = {i: proj.home.get(i, self.mats[i]) for i in self.order_ids}
        self.colours, self.fills, self.satins = dict(proj.colours), dict(proj.fills), dict(proj.satins)
        self.texts, self.groups = dict(proj.texts), dict(proj.groups)
        self.skipped, self.no_outline = set(proj.skipped), set(proj.no_outline)
        self.color_order, self.warnings = list(proj.color_order), list(proj.warnings)
        self.svg_path = proj.source or path
        self.project_path = path
        self.dirty = False
        return _copy.deepcopy(proj.settings)
