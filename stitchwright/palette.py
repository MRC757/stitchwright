"""Brother thread palette helpers and operator colour parsing.

The palette is the 61-colour Brother "Color Thread Conversion Chart"
(see "Thread Colors.pdf", rows 1-61). Rows 62-64 of that chart are
appliqué markers, not threads, and are deliberately left out.

A PES file does not store thread names: its PEC block stores an index into
Brother's fixed 64-entry colour table, and the machine shows the name it
has for that index. pyembroidery picks the index by nearest RGB, so every
thread here uses the exact RGB of its PEC table entry. That makes the
index (and therefore the name on the machine's screen) unambiguous.
"""
from __future__ import annotations

from typing import NamedTuple

from pyembroidery.EmbThread import color_distance_red_mean
from pyembroidery.EmbThreadPec import get_thread_set

RGB = tuple[int, int, int]


class Thread(NamedTuple):
    chart: int        # row number in the conversion chart (1-61)
    name: str         # English colour name from the chart
    number: str       # Brother Embroidery thread number, e.g. "900"
    country: str      # Brother Country thread number, e.g. "100"
    pec_index: int    # slot in the PEC colour table written to the file
    rgb: RGB


# (chart row, name, Embroidery #, Country #, PEC table index)
_CHART = [
    (1, "Black", "900", "100", 20),
    (2, "White", "001", "000", 29),
    (3, "Red", "800", "149", 5),
    (4, "Pink", "085", "155", 43),
    (5, "Deep Rose", "086", "024", 25),
    (6, "Flesh Pink", "124", "152", 42),
    (7, "Dark Fuchsia", "107", "126", 51),
    (8, "Vermilion", "030", "148", 28),
    (9, "Carmine", "807", "158", 48),
    (10, "Royal Purple", "869", "636", 22),
    (11, "Magenta", "620", "625", 7),
    (12, "Salmon Pink", "079", "122", 27),
    (13, "Amber Red", "333", "212", 49),
    (14, "Lemon Yellow", "202", "342", 60),
    (15, "Yellow", "205", "043", 13),
    (16, "Harvest Gold", "206", "334", 58),
    (17, "Orange", "208", "335", 12),
    (18, "Pumpkin", "126", "322", 33),
    (19, "Tangerine", "209", "336", 52),
    (20, "Cream Brown", "010", "331", 18),
    (21, "Cream Yellow", "812", "370", 34),
    (22, "Khaki", "348", "242", 35),
    (23, "Deep Gold", "214", "354", 11),
    (24, "Brass", "328", "404", 15),
    (25, "Linen", "307", "025", 57),
    (26, "Dark Brown", "058", "717", 24),
    (27, "Reddish Brown", "337", "264", 6),
    (28, "Clay Brown", "339", "224", 36),
    (29, "Beige", "843", "012", 47),
    (30, "Warm Gray", "399", "706", 40),
    (31, "Russet Brown", "330", "414", 17),
    (32, "Light Brown", "323", "255", 26),
    (33, "Seacrest", "542", "505", 31),
    (34, "Mint Green", "502", "461", 10),
    (35, "Leaf Green", "509", "463", 37),
    (36, "Olive Green", "519", "476", 50),
    (37, "Dark Olive", "517", "473", 41),
    (38, "Teal Green", "534", "483", 3),
    (39, "Emerald Green", "507", "485", 54),
    (40, "Deep Green", "808", "467", 44),
    (41, "Fresh Green", "027", "442", 61),
    (42, "Lime Green", "513", "444", 14),
    (43, "Moss Green", "515", "446", 56),
    (44, "Blue", "405", "586", 2),
    (45, "Ultra Marine", "406", "575", 21),
    (46, "Light Blue", "017", "150", 53),
    (47, "Sky Blue", "019", "512", 32),
    (48, "Electric Blue", "420", "564", 59),
    (49, "Peacock Blue", "415", "057", 38),
    (50, "Prussian Blue", "007", "588", 1),
    (51, "Cornflower Blue", "070", "015", 4),
    (52, "Light Lilac", "810", "133", 8),
    (53, "Lilac", "612", "623", 9),
    (54, "Violet", "613", "624", 30),
    (55, "Purple", "614", "635", 55),
    (56, "Lavender", "804", "604", 45),
    (57, "Wisteria Violet", "607", "003", 46),
    (58, "Pewter", "704", "745", 19),
    (59, "Dark Gray", "707", "747", 23),
    (60, "Silver", "005", "020", 16),
    (61, "Gray", "817", "734", 39),
]

_THREADS: list[Thread] | None = None


def brother_threads() -> list[Thread]:
    """The 61 Brother threads from the conversion chart, in chart order."""
    global _THREADS
    if _THREADS is None:
        pec = get_thread_set()
        out = []
        for chart, name, number, country, idx in _CHART:
            c = pec[idx].color
            out.append(Thread(chart, name, number, country, idx,
                              ((c >> 16) & 255, (c >> 8) & 255, c & 255)))
        _THREADS = out
    return _THREADS


def nearest_brother(rgb: RGB) -> Thread:
    """The closest chart thread to an RGB colour."""
    return min(brother_threads(), key=lambda t: color_distance_red_mean(*rgb, *t.rgb))


def thread_for_rgb(rgb: RGB) -> Thread | None:
    """The chart thread with exactly this RGB, if any."""
    for t in brother_threads():
        if t.rgb == tuple(rgb):
            return t
    return None


def _norm(name: str) -> str:
    return "".join(name.lower().split())


def parse_color(text: str) -> RGB:
    """Accept '#rrggbb', 'rrggbb', '#rgb', a chart colour name ('Sky Blue'),
    'brother:<thread number>' (e.g. brother:900 or brother:5 for 005 Silver),
    or 'chart:<row>' (1-61, the row number in the conversion chart)."""
    s = text.strip()
    low = s.lower()
    for prefix, field in (("brother:", "number"), ("chart:", "chart")):
        if low.startswith(prefix):
            num = low[len(prefix):].strip()
            if not num.isdigit():
                raise ValueError(f"{prefix} needs a number, got {num!r}")
            for t in brother_threads():
                if int(getattr(t, field)) == int(num):
                    return t.rgb
            what = "thread numbered" if field == "number" else "chart row"
            raise ValueError(f"No Brother {what} {num!r} (see 'stitchwright palette')")
    h = low.lstrip("#")
    if len(h) == 3 and all(c in "0123456789abcdef" for c in h):
        h = "".join(c * 2 for c in h)
    if len(h) == 6 and all(c in "0123456789abcdef" for c in h):
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    for t in brother_threads():
        if _norm(t.name) == _norm(s):
            return t.rgb
    raise ValueError(f"Cannot understand colour {text!r}: use #rrggbb, a Brother colour "
                     "name, brother:<thread number> or chart:<row>")


def to_hex(rgb: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)
