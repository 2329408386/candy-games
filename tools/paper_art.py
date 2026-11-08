"""Builds the watercolor backgrounds of 班级作文报 (sites/teacher/static/paper-art/<key>.<sha8>.svg), one per textbook unit
(sites/teacher/data/paper_themes.json, keyed 年级-册-单元) plus the genre fallbacks and the default, so a paper is
dressed for its topic without any image model at run time.

    python tools/paper_art.py           rebuild every SVG (and delete ones no longer listed)
    python tools/paper_art.py --check   fail when an SVG is missing or out of date, or the themes do not cover every
                                        unit of units.json (run by tests)

Each picture is an A3 landscape sheet (1587×1123): a paper tint, soft color washes in the corners and the theme's
motifs (枫叶, 灯笼, 书包, 火箭 …) arranged around the edges, leaving the middle for the text panel. The motifs are flat
shapes; a turbulence displacement and a light blur give them watercolor edges. Every unit's arrangement is seeded
by its key, so units sharing a theme still look different, and rebuilding gives the same files.
"""

import hashlib, json, math, random, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THEMES = ROOT / "sites" / "teacher" / "data" / "paper_themes.json"
UNITS = ROOT / "sites" / "teacher" / "data" / "units.json"
OUT = ROOT / "sites" / "teacher" / "static" / "paper-art"
W, H = 1587, 1123
LIMITS = {"title": 10, "subject": 16, "tagline": 30, "intro": 90}


def file_name(key, svg):
    """genre:记事 → genre-记事.<sha8>.svg; the content hash in the name is what tools/assets.py expects of media."""
    return key.replace(":", "-") + "." + hashlib.sha256(svg.encode()).hexdigest()[:8] + ".svg"


# ---------------------------------------------------------------- colors
def shade(hex_color, k):
    """k < 1 darker, k > 1 lighter."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    if k < 1:
        r, g, b = (int(c * k) for c in (r, g, b))
    else:
        r, g, b = (int(c + (255 - c) * (k - 1)) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def paint(c, op=0.88):
    return f'fill="{c}" fill-opacity="{op}" stroke="{shade(c, 0.72)}" stroke-opacity=".38" stroke-width="1.3"'


def line(c, w=3, op=0.85):
    return f'fill="none" stroke="{c}" stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round" stroke-opacity="{op}"'


# ---------------------------------------------------------------- motifs: each draws in a 100×100 box around (50, 50)
def maple(c="#d2603a"):
    d = ("M50 8 L58 30 L78 22 L70 42 L92 46 L74 58 L82 74 L60 68 L54 92 L50 72 L46 92 L40 68 L18 74 L26 58 "
         "L8 46 L30 42 L22 22 L42 30 Z")
    return f'<path d="{d}" {paint(c)}/><path d="M50 72 L50 98 M50 60 L30 40 M50 60 L70 40 M50 60 L50 20" {line(shade(c, .7), 1.4, .5)}/>'


def ginkgo(c="#e7b43a"):
    return (f'<path d="M50 92 Q48 70 50 56 Q20 52 14 30 Q30 10 50 18 Q70 10 86 30 Q80 52 50 56" {paint(c)}/>'
            f'<path d="M50 56 L50 18" {line(shade(c, .7), 1.2, .45)}/><path d="M50 92 L50 56" {line("#8a6a3a", 2.4)}/>')


def leaf(c="#7c9a5e", vein=True):
    s = f'<path d="M50 6 Q86 40 50 94 Q14 40 50 6 Z" {paint(c)}/>'
    if vein:
        s += f'<path d="M50 14 L50 88" {line(shade(c, .65), 1.4, .5)}/>'
    return s


def sprig(c_leaf="#7c9a5e", c_branch="#7a5a3f", flower=None, n=5):
    """A curving twig with leaves (and tiny flowers): the frame of a corner."""
    s = f'<path d="M0 50 Q30 40 50 50 T100 46" {line(c_branch, 3.2)}/>'
    for i in range(n):
        x = 12 + i * 80 / max(1, n - 1)
        up = i % 2 == 0
        s += f'<g transform="translate({x:.0f} {44 if up else 54}) rotate({-35 if up else 145}) scale(.26)">{leaf(c_leaf, False)}</g>'
        if flower and i % 2 == 1:
            s += f'<g transform="translate({x - 6:.0f} {(36 if up else 52)}) scale(.13)">{blossom(flower, "#f6d58b")}</g>'
    return s


def moon(c="#f7dc8c"):
    return (f'<circle cx="50" cy="50" r="48" fill="{shade(c, 1.35)}" fill-opacity=".55"/>'
            f'<circle cx="50" cy="50" r="40" {paint(c, .95)}/>'
            f'<circle cx="38" cy="40" r="6" fill="{shade(c, .9)}" fill-opacity=".5"/><circle cx="60" cy="62" r="4" fill="{shade(c, .9)}" fill-opacity=".45"/>')


def sun(c="#f4b942"):
    rays = "".join(
        f'<path d="M{50 + 34 * math.cos(a):.1f} {50 + 34 * math.sin(a):.1f} L{50 + 46 * math.cos(a):.1f} {50 + 46 * math.sin(a):.1f}" {line(c, 4)}/>'
        for a in (i * math.pi / 6 for i in range(12))
    )
    return rays + f'<circle cx="50" cy="50" r="26" {paint(c, .95)}/>'


def lantern(c="#d23a2f", gold="#e3a72f"):
    return (f'<path d="M50 0 L50 14" {line("#5b3a2a", 2)}/><rect x="36" y="12" width="28" height="8" rx="2" {paint(gold)}/>'
            f'<ellipse cx="50" cy="46" rx="34" ry="28" {paint(c, .92)}/>'
            f'<path d="M50 18 Q30 46 50 74 M50 18 Q70 46 50 74" {line(shade(c, .7), 1.4, .5)}/>'
            f'<rect x="36" y="72" width="28" height="8" rx="2" {paint(gold)}/>'
            f'<path d="M44 80 L42 100 M50 80 L50 102 M56 80 L58 100" {line(gold, 2)}/>')


def cloud(c="#e9b25a"):
    return (f'<path d="M14 64 Q8 48 24 44 Q24 26 44 30 Q52 16 66 26 Q86 24 84 44 Q96 50 88 64 Z" {paint(c, .55)}/>'
            f'<path d="M30 56 q6 -8 12 0 q-4 6 -10 2 M58 50 q6 -8 12 0 q-4 6 -10 2" {line(shade(c, .7), 1.6, .55)}/>')


def blossom(c="#f4a6b8", center="#e3a72f"):
    petals = "".join(f'<circle cx="{50 + 22 * math.cos(a):.1f}" cy="{50 + 22 * math.sin(a):.1f}" r="19" {paint(c, .82)}/>'
                     for a in (i * 2 * math.pi / 5 - math.pi / 2 for i in range(5)))
    return petals + f'<circle cx="50" cy="50" r="9" {paint(center, .95)}/>'


def willow(c="#8cbf6a"):
    s = f'<path d="M0 6 Q50 0 100 8" {line("#7a6a4a", 2.4)}/>'
    for i, x in enumerate(range(8, 100, 14)):
        L = 60 + (i * 17) % 34
        s += f'<path d="M{x} 6 Q{x - 6} {L / 2} {x + 2} {L}" {line(c, 1.8, .8)}/>'
        for k in range(3):
            y = 18 + k * L / 4
            s += f'<ellipse cx="{x - 3 + k}" cy="{y:.0f}" rx="2.4" ry="6" {paint(c, .75)} transform="rotate(20 {x} {y:.0f})"/>'
    return s


def kite(c="#e5574f", c2="#4f8fd1"):
    return (f'<path d="M50 4 L80 40 L50 76 L20 40 Z" {paint(c)}/><path d="M50 4 L50 76 M20 40 L80 40" {line("#fff", 1.6, .8)}/>'
            f'<path d="M50 76 Q40 86 52 92 T46 100" {line("#7a6a5a", 1.4)}/>'
            f'<path d="M44 84 l8 -4 l0 8 Z M48 94 l8 -4 l0 8 Z" {paint(c2)}/>')


def swallow(c="#34495e"):
    return (f'<path d="M10 40 Q30 34 46 46 Q62 30 92 26 Q70 40 60 52 Q76 62 86 78 Q62 66 48 58 Q34 62 22 70 Q30 56 38 50 Q24 44 10 40 Z" {paint(c, .9)}/>'
            f'<circle cx="40" cy="45" r="2" fill="#fff"/>')


def backpack(c="#4f86c6"):
    return (f'<path d="M34 14 Q34 4 50 4 Q66 4 66 14" {line(shade(c, .7), 4)}/>'
            f'<rect x="18" y="14" width="64" height="80" rx="16" {paint(c)}/>'
            f'<rect x="28" y="54" width="44" height="30" rx="8" {paint(shade(c, 1.35))}/>'
            f'<path d="M28 64 L72 64" {line(shade(c, .7), 1.5, .6)}/><circle cx="50" cy="36" r="5" {paint("#f2c14e")}/>')


def pencil(c="#f2b134"):
    return (f'<path d="M30 90 L20 100 L24 86 Z" fill="#3b3b3b"/><path d="M24 86 L30 90 L88 32 L82 26 Z" {paint(c)}/>'
            f'<path d="M82 26 L88 32 L96 24 L90 18 Z" {paint("#e88a8a")}/><path d="M24 86 L30 90 L36 84 L30 80 Z" fill="#f3d9b1"/>')


def ruler(c="#e8c27a"):
    ticks = "".join(f'<path d="M{14 + i * 9} 40 L{14 + i * 9} {48 if i % 2 else 54}" {line(shade(c, .55), 1.3, .7)}/>' for i in range(9))
    return f'<rect x="8" y="38" width="86" height="26" rx="3" {paint(c)}/>' + ticks


def basketball(c="#e07b39"):
    return (f'<circle cx="50" cy="50" r="40" {paint(c)}/>'
            f'<path d="M10 50 L90 50 M50 10 L50 90 M22 22 Q40 50 22 78 M78 22 Q60 50 78 78" {line(shade(c, .55), 2, .7)}/>')


def book(c="#6aa57a"):
    return (f'<path d="M8 30 Q30 22 50 32 Q70 22 92 30 L92 82 Q70 74 50 84 Q30 74 8 82 Z" {paint("#fbf7ee", .95)}/>'
            f'<path d="M50 32 L50 84" {line(shade(c, .7), 1.6)}/>'
            f'<path d="M8 82 Q30 74 50 84 Q70 74 92 82 L92 88 Q70 80 50 90 Q30 80 8 88 Z" {paint(c)}/>'
            f'<path d="M16 42 L42 40 M16 52 L42 50 M16 62 L40 60 M58 40 L84 42 M58 50 L84 52 M58 60 L82 62" {line("#9a9a8a", 1.2, .6)}/>')


def books_stack(colors=("#b5574b", "#4f7fa8", "#7a9a5b")):
    s = ""
    for i, c in enumerate(colors):
        y = 70 - i * 20
        x = 14 + (i % 2) * 8
        s += f'<rect x="{x}" y="{y}" width="{70 - i * 6}" height="18" rx="3" {paint(c)}/><path d="M{x + 6} {y + 9} L{x + 60 - i * 6} {y + 9}" {line("#fff", 1.4, .6)}/>'
    return s


def quill(c="#9ab7c9"):
    return (f'<path d="M20 94 Q30 60 80 8 Q92 30 70 50 Q56 66 30 80 Z" {paint(c, .8)}/>'
            f'<path d="M18 98 Q40 60 80 10" {line("#5a6a7a", 1.6, .8)}/>')


def glasses(c="#5a6a7a"):
    return f'<circle cx="28" cy="52" r="18" {line(c, 3)}/><circle cx="72" cy="52" r="18" {line(c, 3)}/><path d="M46 50 Q50 44 54 50 M10 48 L2 40 M90 48 L98 40" {line(c, 3)}/>'


def potted(c_leaf="#6b9e4f", c_pot="#c97a4a"):
    s = "".join(f'<g transform="translate({x} {y}) rotate({r}) scale(.42)">{leaf(c_leaf)}</g>' for x, y, r in ((20, 8, -30), (40, 0, 0), (58, 6, 28), (30, 20, -60), (54, 22, 60)))
    return s + f'<path d="M28 60 L72 60 L66 96 L34 96 Z" {paint(c_pot)}/><rect x="24" y="56" width="52" height="10" rx="3" {paint(shade(c_pot, .85))}/>'


def butterfly(c="#f0b429", c2="#e88a8a"):
    return (f'<ellipse cx="32" cy="36" rx="22" ry="18" {paint(c)} transform="rotate(-20 32 36)"/>'
            f'<ellipse cx="68" cy="36" rx="22" ry="18" {paint(c)} transform="rotate(20 68 36)"/>'
            f'<ellipse cx="36" cy="66" rx="14" ry="12" {paint(c2)}/><ellipse cx="64" cy="66" rx="14" ry="12" {paint(c2)}/>'
            f'<path d="M50 28 L50 80 M50 28 Q44 14 38 10 M50 28 Q56 14 62 10" {line("#4a3a2a", 2)}/>')


def bird(c="#e58b4c"):
    return (f'<ellipse cx="50" cy="56" rx="32" ry="24" {paint(c)}/><circle cx="74" cy="40" r="14" {paint(c)}/>'
            f'<path d="M86 40 L98 44 L86 48 Z" fill="#f2c14e"/><circle cx="77" cy="37" r="2.4" fill="#2c2c2c"/>'
            f'<path d="M30 52 Q46 40 58 56 Q44 66 30 52 Z" {paint(shade(c, .8))}/><path d="M20 58 L4 50 L8 66 Z" {paint(shade(c, .8))}/>')


def fish(c="#f29a38"):
    return (f'<path d="M14 50 Q40 20 72 50 Q40 80 14 50 Z" {paint(c)}/><path d="M70 50 L94 32 L90 50 L94 68 Z" {paint(shade(c, .85))}/>'
            f'<circle cx="28" cy="46" r="3" fill="#2c2c2c"/><path d="M40 40 Q46 50 40 60" {line(shade(c, .7), 1.4, .6)}/>')


def bamboo(c="#6f9d4f", c2="#93bf6e"):
    s = ""
    for x, top in ((30, 0), (56, 10)):
        s += f'<rect x="{x}" y="{top}" width="12" height="{100 - top}" rx="5" {paint(c)}/>'
        for y in range(top + 22, 100, 24):
            s += f'<path d="M{x - 1} {y} L{x + 13} {y}" {line(shade(c, .65), 2, .7)}/>'
    for x, y, r in ((44, 20, 60), (70, 34, -50), (22, 48, -60), (76, 64, 50)):
        s += f'<g transform="translate({x} {y}) rotate({r}) scale(.18 .4)">{leaf(c2, False)}</g>'
    return s


def panda():
    k = "#2c2c2c"
    return (f'<ellipse cx="50" cy="72" rx="34" ry="26" {paint("#fbfbf7", .97)}/>'
            f'<ellipse cx="24" cy="86" rx="10" ry="12" {paint(k)}/><ellipse cx="76" cy="86" rx="10" ry="12" {paint(k)}/>'
            f'<circle cx="26" cy="20" r="11" {paint(k)}/><circle cx="74" cy="20" r="11" {paint(k)}/>'
            f'<circle cx="50" cy="38" r="28" {paint("#fbfbf7", .97)}/>'
            f'<ellipse cx="38" cy="38" rx="8" ry="10" {paint(k)} transform="rotate(-25 38 38)"/><ellipse cx="62" cy="38" rx="8" ry="10" {paint(k)} transform="rotate(25 62 38)"/>'
            f'<circle cx="39" cy="38" r="2.6" fill="#fff"/><circle cx="61" cy="38" r="2.6" fill="#fff"/><ellipse cx="50" cy="50" rx="5" ry="3.4" fill="{k}"/>'
            f'<ellipse cx="34" cy="52" rx="5" ry="3" fill="#f2a7a7" fill-opacity=".6"/><ellipse cx="66" cy="52" rx="5" ry="3" fill="#f2a7a7" fill-opacity=".6"/>')


def star(c="#f4c542"):
    pts = " ".join(f"{50 + (44 if i % 2 == 0 else 19) * math.cos(-math.pi / 2 + i * math.pi / 5):.1f},{50 + (44 if i % 2 == 0 else 19) * math.sin(-math.pi / 2 + i * math.pi / 5):.1f}" for i in range(10))
    return f'<polygon points="{pts}" {paint(c, .92)}/>'


def castle(c="#b9a3d9", roof="#f2a7c3"):
    return (f'<rect x="14" y="44" width="72" height="52" {paint(c)}/><rect x="8" y="30" width="20" height="66" {paint(shade(c, .9))}/>'
            f'<rect x="72" y="30" width="20" height="66" {paint(shade(c, .9))}/><path d="M6 32 L18 8 L30 32 Z M70 32 L82 8 L94 32 Z M34 46 L50 18 L66 46 Z" {paint(roof)}/>'
            f'<path d="M42 96 L42 74 Q50 64 58 74 L58 96 Z" {paint(shade(c, .7))}/><path d="M18 8 L18 0 L26 3 L18 6 M82 8 L82 0 L90 3 L82 6" {line("#e2655d", 1.6)}/>')


def wand(c="#7b5aa6"):
    return f'<path d="M14 92 L64 40" {line(c, 5)}/><g transform="translate(52 0) scale(.5)">{star()}</g>' + "".join(
        f'<circle cx="{x}" cy="{y}" r="2.4" fill="#f4c542" fill-opacity=".8"/>' for x, y in ((84, 54), (94, 34), (70, 66)))


def rocket(c="#e9654d"):
    return (f'<path d="M50 4 Q74 26 70 70 L30 70 Q26 26 50 4 Z" {paint("#f6f3ee", .97)}/><path d="M50 4 Q62 14 66 26 L34 26 Q38 14 50 4 Z" {paint(c)}/>'
            f'<circle cx="50" cy="42" r="9" {paint("#7cc6d9")}/><path d="M30 52 L16 76 L30 70 Z M70 52 L84 76 L70 70 Z" {paint(c)}/>'
            f'<path d="M38 72 Q50 104 62 72 Z" {paint("#f2b134", .9)}/>')


def planet(c="#f3b54a", ring="#8f7fd1"):
    return (f'<circle cx="50" cy="50" r="28" {paint(c)}/><ellipse cx="50" cy="52" rx="46" ry="12" {line(ring, 4, .8)} transform="rotate(-18 50 52)"/>'
            f'<path d="M30 44 Q50 36 70 44" {line(shade(c, .75), 2, .5)}/>')


def flask(c="#7cc6d9"):
    return (f'<path d="M40 8 L60 8 L60 38 L86 88 Q88 96 80 96 L20 96 Q12 96 14 88 L40 38 Z" {paint("#eef6f8", .9)}/>'
            f'<path d="M28 66 L72 66 L86 88 Q88 96 80 96 L20 96 Q12 96 14 88 Z" {paint(c)}/>'
            f'<circle cx="44" cy="80" r="4" fill="#fff" fill-opacity=".8"/><circle cx="58" cy="74" r="3" fill="#fff" fill-opacity=".8"/><circle cx="52" cy="54" r="3" {paint(c)}/>')


def mountains(c="#7fa596", c2="#a9c4b5"):
    return (f'<path d="M0 100 L26 40 L44 70 L66 22 L100 100 Z" {paint(c2, .7)}/>'
            f'<path d="M-10 100 L18 58 L36 80 L56 50 L84 100 Z" {paint(c, .8)}/>'
            f'<path d="M62 28 L66 22 L71 30" {line("#ffffff", 3, .8)}/>')


def bridge(c="#8c7a6b", water="#9cc7dd"):
    return (f'<path d="M0 70 Q50 20 100 70" {line(c, 7)}/><path d="M0 70 L100 70" {line(c, 4)}/>'
            + "".join(f'<path d="M{x} {70 - 50 * (1 - ((x - 50) / 50) ** 2) * .9 + 4:.0f} L{x} 70" {line(c, 2, .7)}/>' for x in range(14, 90, 12))
            + f'<path d="M0 84 Q25 78 50 84 T100 84 M10 94 Q35 88 60 94 T110 94" {line(water, 2.4, .8)}/>')


def pine(c="#5f8f6b"):
    return f'<path d="M50 4 L74 40 L62 40 L82 70 L56 70 L56 96 L44 96 L44 70 L18 70 L38 40 L26 40 Z" {paint(c)}/>'


def envelope(c="#fbf3e6", line_c="#c97f6a"):
    return (f'<rect x="8" y="22" width="84" height="58" rx="5" {paint(c, .97)}/><path d="M8 26 L50 58 L92 26" {line(line_c, 2.2)}/>'
            f'<path d="M50 40 C44 32 34 40 50 52 C66 40 56 32 50 40 Z" {paint("#e2655d")}/>')


def paper_plane(c="#7fb0d8"):
    return (f'<path d="M6 50 L94 14 L60 90 L46 60 Z" {paint(c, .85)}/><path d="M94 14 L46 60 L40 82 L56 66" {paint(shade(c, .85))}/>'
            f'<path d="M4 70 Q20 60 30 74" {line(c, 1.6, .5)}/>')


def stamp(c="#e2655d"):
    return f'<rect x="20" y="16" width="60" height="70" rx="3" {paint("#fff8ee", .97)} stroke-dasharray="4 3"/><g transform="translate(30 30) scale(.4)">{blossom(c)}</g>'


def heart(c="#e2655d"):
    return f'<path d="M50 88 C10 60 6 30 28 18 C40 12 48 20 50 30 C52 20 60 12 72 18 C94 30 90 60 50 88 Z" {paint(c)}/>'


def balloon(c="#e85d5d"):
    return (f'<ellipse cx="50" cy="38" rx="28" ry="34" {paint(c)}/><path d="M46 72 L54 72 L50 78 Z" {paint(c)}/>'
            f'<path d="M50 78 Q42 88 52 96 T48 104" {line("#8a8a8a", 1.4)}/><ellipse cx="40" cy="26" rx="6" ry="10" fill="#fff" fill-opacity=".45"/>')


def bunting(colors=("#e85d5d", "#4fa3d9", "#f2c14e", "#69b36b", "#b48ad6")):
    s = f'<path d="M0 10 Q50 34 100 10" {line("#8a7a6a", 1.6)}/>'
    for i, x in enumerate(range(6, 96, 18)):
        y = 10 + 24 * (1 - ((x - 50) / 50) ** 2) * .9
        s += f'<path d="M{x} {y:.0f} L{x + 14} {y + 2:.0f} L{x + 6} {y + 22:.0f} Z" {paint(colors[i % len(colors)])}/>'
    return s


def football(c="#3b3b3b"):
    return (f'<circle cx="50" cy="50" r="38" {paint("#fbfbf7", .97)}/><polygon points="50,34 64,44 59,60 41,60 36,44" {paint(c)}/>'
            f'<path d="M50 34 L50 14 M64 44 L84 38 M59 60 L70 78 M41 60 L30 78 M36 44 L16 38" {line(c, 2, .7)}/>')


def blocks():
    return (f'<rect x="10" y="56" width="38" height="38" rx="4" {paint("#e85d5d")}/><rect x="52" y="56" width="38" height="38" rx="4" {paint("#4fa3d9")}/>'
            f'<rect x="30" y="16" width="38" height="38" rx="4" {paint("#f2c14e")}/>'
            f'<path d="M22 66 L36 66 M29 66 L29 84 M62 66 L78 84 M78 66 L62 84" {line("#fff", 3, .8)}/><circle cx="49" cy="35" r="9" {line("#fff", 3, .8)}/>')


def teddy(c="#c48a5a"):
    return (f'<circle cx="24" cy="18" r="12" {paint(c)}/><circle cx="76" cy="18" r="12" {paint(c)}/><ellipse cx="50" cy="74" rx="30" ry="24" {paint(c)}/>'
            f'<circle cx="50" cy="38" r="28" {paint(c)}/><ellipse cx="50" cy="48" rx="12" ry="9" {paint(shade(c, 1.4))}/>'
            f'<circle cx="40" cy="32" r="3" fill="#2c2c2c"/><circle cx="60" cy="32" r="3" fill="#2c2c2c"/><ellipse cx="50" cy="44" rx="4" ry="3" fill="#2c2c2c"/>'
            f'<ellipse cx="50" cy="76" rx="14" ry="12" {paint(shade(c, 1.4))}/>')


def top_toy(c="#b48ad6"):
    return (f'<path d="M50 8 L50 22" {line("#7a5a3f", 4)}/><path d="M14 40 Q50 18 86 40 L50 94 Z" {paint(c)}/>'
            f'<path d="M14 40 Q50 58 86 40" {line("#fff", 3, .7)}/><path d="M24 54 Q50 70 76 54" {line("#f2c14e", 3, .8)}/>')


def sunflower(c="#f2b42c", center="#7a4f2a"):
    petals = "".join(f'<ellipse cx="{50 + 28 * math.cos(a):.1f}" cy="{50 + 28 * math.sin(a):.1f}" rx="14" ry="7" {paint(c)} transform="rotate({math.degrees(a):.0f} {50 + 28 * math.cos(a):.1f} {50 + 28 * math.sin(a):.1f})"/>'
                     for a in (i * math.pi / 7 for i in range(14)))
    return petals + f'<circle cx="50" cy="50" r="18" {paint(center, .95)}/>'


def crane(c="#e88a8a"):
    return (f'<path d="M6 60 L44 46 L50 20 L58 46 L94 34 L64 58 L80 82 L50 64 L36 70 Z" {paint(c, .85)}/>'
            f'<path d="M44 46 L50 64 L58 46" {line(shade(c, .7), 1.4, .6)}/><path d="M80 82 L92 78" {line(shade(c, .7), 2)}/>')


def dots(colors):
    rnd = random.Random(7)
    return "".join(f'<circle cx="{rnd.uniform(5, 95):.0f}" cy="{rnd.uniform(5, 95):.0f}" r="{rnd.uniform(3, 7):.1f}" {paint(rnd.choice(colors), .8)}/>' for _ in range(9))


MOTIFS = {
    "maple": maple, "maple2": lambda: maple("#c94c2f"), "ginkgo": ginkgo, "leaf": leaf, "moon": moon, "sun": sun,
    "lantern": lantern, "cloud": cloud, "blossom": blossom, "plum": lambda: blossom("#e7708a", "#f6d58b"),
    "willow": willow, "kite": kite, "swallow": swallow, "backpack": backpack, "pencil": pencil, "ruler": ruler,
    "basketball": basketball, "book": book, "books": books_stack, "quill": quill, "glasses": glasses, "potted": potted,
    "butterfly": butterfly, "bird": bird, "fish": fish, "bamboo": bamboo, "panda": panda, "star": star, "castle": castle,
    "wand": wand, "rocket": rocket, "planet": planet, "flask": flask, "mountains": mountains, "bridge": bridge,
    "pine": pine, "envelope": envelope, "plane": paper_plane, "stamp": stamp, "heart": heart, "balloon": balloon,
    "balloon2": lambda: balloon("#4fa3d9"), "balloon3": lambda: balloon("#f2c14e"), "bunting": bunting,
    "football": football, "blocks": blocks, "teddy": teddy, "top": top_toy, "sunflower": sunflower, "crane": crane,
    "pinkleaf": lambda: leaf("#e8a0a8"), "lightleaf": lambda: leaf("#9cc47a"), "redleaf": lambda: leaf("#d9733b"),
    "purplestar": lambda: star("#c4a6ea"), "bluestar": lambda: star("#8fc3ea"),
}

# ---------------------------------------------------------------- themes: paper tint, washes, the frame twig, big corner motifs, small scatter
ART = {
    "autumn": dict(ground="maple", paper="#f7efdf", washes=["#efc56a", "#e3925a", "#b7c48a", "#f4d995"], twig=("#7c9a5e", "#7a5a3f", "#f0b429"),
                   big=["moon", "lantern", "maple", "ginkgo"], small=["maple", "maple2", "ginkgo", "redleaf", "leaf"]),
    "spring": dict(ground="blossom", paper="#f3f6ea", washes=["#c4e3b4", "#f6cbd3", "#bfdcef", "#e6f0c0"], twig=("#8cbf6a", "#7a6a4a", "#f4a6b8"),
                   big=["kite", "willow", "blossom", "swallow"], small=["blossom", "pinkleaf", "lightleaf", "swallow", "butterfly"]),
    "festival": dict(ground="cloud", paper="#fbf0e3", washes=["#f2b29e", "#f6d488", "#ecc4c4", "#f3dcb4"], twig=("#6f8f5a", "#5b3a2a", "#e7708a"),
                     big=["lantern", "lantern", "cloud", "plum"], small=["plum", "cloud", "lantern", "blossom"]),
    "classmates": dict(ground="lightleaf", paper="#f4f1e7", washes=["#bcd6ea", "#f3d9a4", "#cfe3c1", "#f2c9b8"], twig=("#7fae6a", "#7a5a3f", None),
                       big=["backpack", "basketball", "book", "books"], small=["pencil", "ruler", "lightleaf", "star", "glasses"]),
    "plants": dict(ground="lightleaf", paper="#f0f5e8", washes=["#c9e2b3", "#e8f0c7", "#f6e3b5", "#d8ecd0"], twig=("#6b9e4f", "#6a5a3a", "#f2a2b5"),
                   big=["potted", "sunflower", "butterfly", "potted"], small=["leaf", "lightleaf", "butterfly", "blossom"]),
    "animals": dict(ground="lightleaf", paper="#f5f1e5", washes=["#d8e8c5", "#f2dcb3", "#c7dff0", "#f3d0c0"], twig=("#7fae6a", "#7a5a3f", None),
                    big=["bird", "fish", "butterfly", "bird"], small=["leaf", "lightleaf", "fish", "butterfly", "blossom"]),
    "panda": dict(ground="bamboo", paper="#f0f4e8", washes=["#cfe6bd", "#e2eed5", "#d6e9cf", "#f1efd4"], twig=("#6f9d4f", "#6f9d4f", None),
                  big=["panda", "bamboo", "bamboo", "panda"], small=["lightleaf", "leaf", "bamboo"]),
    "fairy": dict(ground="star", paper="#f3eff7", washes=["#d7c9ee", "#f7d6e4", "#c9e1f5", "#f4e6b8"], twig=("#9cc47a", "#8a6aa8", "#f2a7c3"),
                  big=["castle", "moon", "wand", "star"], small=["star", "purplestar", "bluestar", "butterfly", "blossom"]),
    "science": dict(ground="bluestar", paper="#eef3f7", washes=["#bcd7ee", "#d6cdee", "#c8ecdf", "#f4ddb4"], twig=("#7fb0a0", "#6a7a8a", None),
                    big=["rocket", "planet", "flask", "planet"], small=["star", "bluestar", "purplestar", "flask"]),
    "landscape": dict(ground="pine", paper="#eef3ec", washes=["#b8d4c5", "#d5e6d0", "#c7dbe8", "#f0e2c0"], twig=("#7fae6a", "#6a5a4a", None),
                      big=["mountains", "bridge", "pine", "sun"], small=["pine", "lightleaf", "swallow", "cloud"]),
    "books": dict(ground="lightleaf", paper="#f6efe2", washes=["#e8d2a6", "#cfe0c9", "#e6c3b3", "#d4dce9"], twig=("#7f9f6a", "#7a5a3f", None),
                  big=["books", "book", "quill", "glasses"], small=["lightleaf", "leaf", "star", "pencil"]),
    "letters": dict(ground="blossom", paper="#f7f0e8", washes=["#f2cfc4", "#d4e3f0", "#f5e3b0", "#e6d6ef"], twig=("#8fb57a", "#8a6a5a", "#f2a7b5"),
                    big=["envelope", "plane", "stamp", "heart"], small=["heart", "plane", "blossom", "star"]),
    "play": dict(ground="lightleaf", paper="#f6f3e8", washes=["#f6d38a", "#bfe0ee", "#f4c3c3", "#cfe7c2"], twig=("#7fae6a", "#7a5a3f", None),
                 big=["balloon", "bunting", "football", "balloon2"], small=["balloon3", "star", "balloon", "basketball"]),
    "toys": dict(ground="star", paper="#f6f0e5", washes=["#f3d2a2", "#cbe2f2", "#f5c8d4", "#d9ecc4"], twig=("#8fb57a", "#8a6a5a", None),
                 big=["teddy", "blocks", "top", "balloon2"], small=["star", "blocks", "balloon", "heart"]),
    "growth": dict(ground="lightleaf", paper="#f7f1e4", washes=["#f7d98a", "#f3c1a8", "#cfe3c1", "#f2d2dc"], twig=("#7fae6a", "#7a5a3f", "#f2b42c"),
                   big=["sunflower", "sun", "crane", "heart"], small=["heart", "lightleaf", "crane", "star", "butterfly"]),
    "colorful": dict(ground="blossom", paper="#f7f3ea", washes=["#f6c6c6", "#c6e0f6", "#f6e7a6", "#c9eac3"], twig=("#8fb57a", "#7a5a3f", "#f4a6b8"),
                     big=["butterfly", "blossom", "star", "sunflower"], small=["star", "blossom", "butterfly", "heart", "bluestar"]),
}
ACCENT = {  # the paper's title color per theme (the page reads it from paper_themes via accent())
    "autumn": "#9a5b1e", "spring": "#3f7a4a", "festival": "#b0322a", "classmates": "#2f5f8a", "plants": "#3f7a3a",
    "animals": "#8a5a2a", "panda": "#2f6a3a", "fairy": "#6a4a9a", "science": "#2f5f8a", "landscape": "#2f6a5a",
    "books": "#7a4a2a", "letters": "#a5483c", "play": "#c0503a", "toys": "#b0603a", "growth": "#b06a1a", "colorful": "#7a4a8a",
}


def place(motif, x, y, size, rot=0, flip=False):
    s = size / 100
    fx = f" scale(-1 1)" if flip else ""
    return f'<g transform="translate({x:.0f} {y:.0f}) rotate({rot:.0f}) scale({s:.3f}){fx} translate(-50 -50)">{MOTIFS[motif]()}</g>'


def twig(theme, x, y, length, rot):
    leaf_c, branch_c, flower = theme["twig"]
    s = length / 100
    return f'<g transform="translate({x:.0f} {y:.0f}) rotate({rot:.0f}) scale({s:.3f} {s * .9:.3f}) translate(0 -50)">{sprig(leaf_c, branch_c, flower)}</g>'


def build(key, art):
    theme = ART[art]
    rnd = random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:12], 16))
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" preserveAspectRatio="xMidYMid slice">',
           '<defs>',
           '<filter id="wc" x="-10%" y="-10%" width="120%" height="120%"><feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="3"/>'
           '<feDisplacementMap in="SourceGraphic" scale="5" xChannelSelector="R" yChannelSelector="G"/><feGaussianBlur stdDeviation=".7"/></filter>',
           '<filter id="wash" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="46"/></filter>',
           '<filter id="grain"><feTurbulence type="fractalNoise" baseFrequency=".8" numOctaves="2" seed="9"/>'
           '<feColorMatrix values="0 0 0 0 .45  0 0 0 0 .38  0 0 0 0 .3  0 0 0 .06 0"/></filter>',
           '</defs>',
           f'<rect width="{W}" height="{H}" fill="{theme["paper"]}"/>']
    # washes: big soft blobs in the corners and along the edges
    spots = [(0, 0), (W, 0), (0, H), (W, H), (W / 2, -60), (W / 2, H + 60), (-60, H / 2), (W + 60, H / 2)]
    for i, (x, y) in enumerate(spots):
        c = theme["washes"][i % len(theme["washes"])]
        out.append(f'<ellipse cx="{x + rnd.uniform(-60, 60):.0f}" cy="{y + rnd.uniform(-40, 40):.0f}" rx="{rnd.uniform(220, 340):.0f}" ry="{rnd.uniform(160, 260):.0f}" '
                   f'fill="{c}" fill-opacity="{rnd.uniform(.45, .65):.2f}" filter="url(#wash)"/>')
    art_parts = []
    hill = theme["washes"]
    for i, (base, amp, op) in enumerate(((H - 70, 46, .55), (H - 34, 30, .7))):
        pts, x = [], -20
        while x <= W + 40:
            pts.append(f"{x:.0f} {base - amp * (0.5 + 0.5 * math.sin(x / rnd.uniform(140, 220) + i * 2 + rnd.uniform(0, .3))):.0f}")
            x += 60
        out.append(f'<path d="M-20 {H} L{" L".join(pts)} L{W + 40} {H} Z" fill="{shade(hill[(i + 2) % len(hill)], .92)}" fill-opacity="{op}" filter="url(#wc)"/>')
    ground = theme.get("ground")
    if ground:
        for x in range(160, W - 160, 210):
            art_parts.append(place(ground, x + rnd.uniform(-50, 50), H - rnd.uniform(40, 70), rnd.uniform(70, 110), 0, rnd.random() < .5))
    # corner twigs framing the sheet
    for (cx, cy, r1, r2) in ((0, 0, 8, 80), (W, 0, 172, 100), (0, H, -8, -80), (W, H, 188, 260)):
        art_parts.append(twig(theme, cx, cy + (34 if cy == 0 else -34), rnd.uniform(300, 400), r1 + rnd.uniform(-6, 6)))
        art_parts.append(twig(theme, cx + (34 if cx == 0 else -34), cy, rnd.uniform(240, 320), r2 + rnd.uniform(-6, 6)))
    # big motifs: one per corner, order shuffled per unit
    bigs = theme["big"][:]
    rnd.shuffle(bigs)
    corners = [(140, 135), (W - 150, 135), (140, H - 150), (W - 150, H - 150)]
    upright = ("rocket", "castle", "panda", "lantern", "potted", "pine", "mountains", "bridge", "books", "teddy", "balloon", "balloon2", "sunflower")
    for k, ((x, y), m) in enumerate(zip(corners, bigs)):
        art_parts.append(place(m, x + rnd.uniform(-20, 20), y + rnd.uniform(-15, 15), rnd.uniform(210, 260),
                               0 if m in upright else rnd.uniform(-12, 12), rnd.random() < .5 and m not in upright))
        m2 = bigs[(k + 1) % len(bigs)]
        dx, dy = (1 if x < W / 2 else -1), (1 if y < H / 2 else -1)
        art_parts.append(place(m2, x + dx * rnd.uniform(150, 190), y - dy * rnd.uniform(40, 70), rnd.uniform(110, 140),
                               0 if m2 in upright else rnd.uniform(-20, 20), rnd.random() < .5 and m2 not in upright))
    # small motifs scattered in the margins (top/bottom band, left/right band), clear of the corners
    def scatter(n, xs, ys):
        for _ in range(n):
            m = rnd.choice(theme["small"])
            art_parts.append(place(m, rnd.uniform(*xs), rnd.uniform(*ys), rnd.uniform(34, 58), rnd.uniform(-40, 40), rnd.random() < .5))
    scatter(4, (300, W - 300), (6, 40))
    scatter(4, (300, W - 300), (H - 40, H - 8))
    scatter(3, (6, 36), (280, H - 280))
    scatter(3, (W - 36, W - 6), (280, H - 280))
    # little extras around each corner
    for (x, y) in corners:
        for _ in range(3):
            m = rnd.choice(theme["small"])
            art_parts.append(place(m, x + rnd.uniform(-110, 140) * (1 if x < W / 2 else -1), y + rnd.uniform(-100, 140) * (1 if y < H / 2 else -1),
                                   rnd.uniform(36, 60), rnd.uniform(-50, 50), rnd.random() < .5))
    out.append(f'<g filter="url(#wc)">{"".join(art_parts)}</g>')
    out.append(f'<rect width="{W}" height="{H}" filter="url(#grain)"/>')
    out.append("</svg>")
    svg = "".join(out)
    return re.sub(r"(\d+\.\d{2})\d+", r"\1", svg) + "\n"


def themes():
    return json.loads(THEMES.read_text(encoding="utf-8"))


def problems(data):
    units = json.loads(UNITS.read_text(encoding="utf-8"))
    wanted = {f"{g}-{t}-{u['unit']}" for g in units for t in units[g] for u in units[g][t]}
    out = [f"paper_themes.json 少了单元 {k}" for k in sorted(wanted - set(data))]
    if "default" not in data:
        out.append("paper_themes.json 少了 default")
    for k, v in data.items():
        if v.get("art") not in ART:
            out.append(f"{k}: 不认识的图案 {v.get('art')}")
        for field, limit in LIMITS.items():
            if not v.get(field) or len(v[field]) > limit:
                out.append(f"{k}: {field} 要有，且不超过 {limit} 字")
    return out


def main():
    data = themes()
    bad = problems(data)
    if bad:
        sys.exit("\n".join(bad))
    files = {}
    for k, v in data.items():
        svg = build(k, v["art"])
        files[file_name(k, svg)] = svg
    if "--check" in sys.argv:
        stale = [n for n, svg in files.items() if not (OUT / n).is_file() or (OUT / n).read_text(encoding="utf-8") != svg]
        extra = [p.name for p in OUT.glob("*.svg") if p.name not in files] if OUT.is_dir() else []
        if stale or extra:
            sys.exit(f"作文报背景图不是最新的（{len(stale)} 张要重画，{len(extra)} 张多余），请运行 python tools/paper_art.py")
        print("paper art ok")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    for p in OUT.glob("*.svg"):
        if p.name not in files:
            p.unlink()
    for name, svg in files.items():
        (OUT / name).write_text(svg, encoding="utf-8")
    print(f"wrote {len(files)} files to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
