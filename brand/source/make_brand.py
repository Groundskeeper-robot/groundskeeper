"""Groundskeeper logo set: the GK mark over grass, the wordmark (real IBM Plex Sans Condensed outlines, shaped by HarfBuzz),
lockups, favicons and a social card. Writes SVGs to OUT; PNGs are rendered afterwards by render.mjs."""
import math, pathlib, sys
import uharfbuzz as hb
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

HERE = pathlib.Path(__file__).parent
OUT = pathlib.Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
SEMI = str(HERE / "fonts" / "IBMPlexSansCondensed-SemiBold.ttf")
MED = str(HERE / "fonts" / "IBMPlexSansCondensed-Medium.ttf")

# Brand colours. The first four are new; the rest are the panel's own tokens (web/style.css).
TURF = "#1F3A28"        # the tile
CLIPPING = "#8BD49A"    # the G on dark grounds (= --ok, dark theme)
CLIPPING_DEEP = "#22703A"  # the G on light grounds (= --link, light theme)
ROBOT = "#79B8F0"       # the robot dot on dark grounds (= --robot / --info, dark)
ROBOT_DEEP = "#2A74B8"  # the robot dot on light grounds (= --info, light)
INK_DARK_BG = "#E6ECE8" # wordmark on dark (= --text, dark)
INK_LIGHT_BG = "#16201B"  # wordmark on light (= --text, light)
MUTED_DARK = "#A3B1AA"  # = --muted, dark
MUTED_LIGHT = "#56655D" # = --muted, light
BG_DARK = "#0E1311"     # = --bg, dark
BG_LIGHT = "#F6F8F7"    # = --bg, light


def fmt(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


# ---------------------------------------------------------------- the mark (64-unit grid)
# A GK monogram (IBM Plex Sans Condensed Bold) over a strip of grass. The robot (a dot in the Live map's robot blue)
# sits in the grass: short and mowed behind it on the left, still long ahead of it on the right.
BOLD = str(HERE / "fonts" / "IBMPlexSansCondensed-Bold.ttf")
GRASS = "#8BD49A"        # front blades on the tile and on dark grounds (= --ok, dark)
GRASS_BACK = "#4F8F5E"   # back blades on the tile and on dark grounds
GRASS_DEEP = "#2E8A4A"   # front blades on light grounds (= --ok, light)
GRASS_BACK_LIGHT = "#A9D3B3"  # back blades on light grounds
MOW_X = 24               # left of this the robot has already mowed
DOT = (26.5, 55, 4.5)
DOT_SIMPLE = (25.5, 54, 5.5)


def blades(rng, x0, x1, spacing, w, tip_lo, tip_hi, base, lean):
    out, x = [], x0
    while x < x1:
        tip, l = rng.uniform(tip_lo, tip_hi), rng.uniform(-lean, lean)
        mid = (tip + base) / 2
        out.append(f"M{fmt(x - w / 2)} {base}Q{fmt(x - w / 4 + l / 2)} {fmt(mid)} {fmt(x + l)} {fmt(tip)}"
                   f"Q{fmt(x + w / 4 + l / 2)} {fmt(mid)} {fmt(x + w / 2)} {base}Z")
        x += spacing * rng.uniform(0.85, 1.15)
    return "".join(out)


def grass(front, back, simple=False):
    rng, base = __import__("random").Random(7), 66
    if simple:   # 16 to 23 px: a few chunky blades read; thin ones turn to mush
        d = blades(rng, 2, 22, 7, 8, 56.5, 58.5, base, 1) + blades(rng, 32, 68, 7.5, 9.5, 45, 49, base, 2)
        return f'<path fill="{front}" d="M0 59.5H64V66H0Z{d}"/>'
    back_d = blades(rng, 0, MOW_X, 3, 4.5, 57, 59, base, 1) + blades(rng, MOW_X, 66, 3.2, 5, 47, 52, base, 2)
    front_d = blades(rng, 1.5, MOW_X - 2, 3.4, 4.5, 58, 60.5, base, 1) + blades(rng, MOW_X + 6, 66, 3.6, 5.5, 44, 50, base, 2.5)
    ground = "M0 61H64V66H0Z"   # the turf the blades grow from, so they read as lawn, not flames
    return (f'<path fill="{back}" d="{back_d}"/>' if back else "") + f'<path fill="{front}" d="{ground}{front_d}"/>'


def gk(ink, simple=False):
    size, base = (40, 41) if simple else (37, 40)
    _, w, _, _ = text_path(BOLD, "GK", size, 0, base, 0.01)
    d, _, _, _ = text_path(BOLD, "GK", size, (64 - w) / 2, base, 0.01)
    return f'<path fill="{ink}" d="{d}"/>'


def dot(ink, simple=False):
    cx, cy, r = DOT_SIMPLE if simple else DOT
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{ink}"/>'


def glyph(letters, front, back, dot_ink, simple=False):
    """The monogram, grass and robot without the tile."""
    return gk(letters, simple) + grass(front, back, simple) + dot(dot_ink, simple)


def tile(x=0, y=0, scale=1.0, simple=False, uid="t"):
    t = f' transform="translate({fmt(x)} {fmt(y)}) scale({fmt(scale)})"' if (x or y or scale != 1) else ""
    return (f'<g{t}><clipPath id="{uid}"><rect width="64" height="64" rx="14"/></clipPath>'
            f'<rect width="64" height="64" rx="14" fill="{TURF}"/>'
            f'<g clip-path="url(#{uid})">{gk(INK_DARK_BG, simple)}{grass(GRASS, GRASS_BACK, simple)}{dot(ROBOT, simple)}</g></g>')


# ---------------------------------------------------------------- type as outlines
def text_path(fontfile, text, size, x0, baseline, tracking_em=0.0):
    blob = hb.Blob.from_file_path(fontfile)
    face = hb.Face(blob)
    font = hb.Font(face)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, {"kern": True, "liga": True})
    tt = TTFont(fontfile)
    gs, order, upem = tt.getGlyphSet(), tt.getGlyphOrder(), face.upem
    s = size / upem
    x, parts = 0.0, []
    infos, poss = buf.glyph_infos, buf.glyph_positions
    for i, (info, pos) in enumerate(zip(infos, poss)):
        pen = SVGPathPen(gs, ntos=fmt)
        gs[order[info.codepoint]].draw(TransformPen(pen, (s, 0, 0, -s, x0 + (x + pos.x_offset) * s, baseline - pos.y_offset * s)))
        parts.append(pen.getCommands())
        x += pos.x_advance + (tracking_em * upem if i < len(infos) - 1 else 0)
    os2 = tt["OS/2"]
    return " ".join(p for p in parts if p), x * s, os2.sCapHeight * s, -tt["hhea"].descent * s


def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {fmt(w)} {fmt(h)}" width="{fmt(w)}" height="{fmt(h)}" '
            f'role="img" aria-label="{title}"><title>{title}</title>{body}</svg>\n')


def write(name, content):
    (OUT / name).write_text(content, encoding="utf-8")
    print("wrote", name)


NAME, TRACK = "Groundskeeper", -0.005

# marks
write("groundskeeper-mark.svg", svg(64, 64, tile(uid="m"), "Groundskeeper"))
write("groundskeeper-mark-small.svg", svg(64, 64, tile(simple=True, uid="ms"), "Groundskeeper"))
write("groundskeeper-glyph-on-dark.svg", svg(64, 64, glyph(INK_DARK_BG, GRASS, GRASS_BACK, ROBOT), "Groundskeeper"))
write("groundskeeper-glyph-on-light.svg", svg(64, 64, glyph(INK_LIGHT_BG, GRASS_DEEP, GRASS_BACK_LIGHT, ROBOT_DEEP), "Groundskeeper"))
write("groundskeeper-glyph-mono-dark.svg", svg(64, 64, glyph(INK_LIGHT_BG, INK_LIGHT_BG, None, INK_LIGHT_BG), "Groundskeeper"))
write("groundskeeper-glyph-mono-light.svg", svg(64, 64, glyph(INK_DARK_BG, INK_DARK_BG, None, INK_DARK_BG), "Groundskeeper"))

# wordmarks and lockups
for ground, ink, muted in (("dark", INK_DARK_BG, MUTED_DARK), ("light", INK_LIGHT_BG, MUTED_LIGHT)):
    # wordmark alone
    d, w, cap, desc = text_path(SEMI, NAME, 48, 0, 48 * 0.72, TRACK)
    hgt = 48 * 0.72 + desc
    write(f"groundskeeper-wordmark-on-{ground}.svg", svg(w, hgt, f'<path fill="{ink}" d="{d}"/>', "Groundskeeper"))

    # horizontal lockup: 64 mark, 18 gap, caps centred on the mark (cap height = 45% of the mark)
    size = 42
    d, w, cap, desc = text_path(SEMI, NAME, size, 64 + 18, 0, TRACK)
    base = (64 - cap) / 2 + cap
    d, w, cap, desc = text_path(SEMI, NAME, size, 64 + 18, base, TRACK)
    width = 64 + 18 + w
    write(f"groundskeeper-lockup-on-{ground}.svg",
          svg(width, 64, tile(uid=f"h{ground}") + f'<path fill="{ink}" d="{d}"/>', "Groundskeeper"))

    # endorsed lockup: the same, plus "for Yarbo" in Medium, muted, on the same baseline
    d2, w2, _, _ = text_path(MED, "for Yarbo", size * 0.55, width + size * 0.28, base, 0.0)
    write(f"groundskeeper-lockup-for-yarbo-on-{ground}.svg",
          svg(width + size * 0.28 + w2, 64, tile(uid=f"e{ground}") + f'<path fill="{ink}" d="{d}"/><path fill="{muted}" d="{d2}"/>',
              "Groundskeeper for Yarbo"))

    # stacked lockup: mark over the name, centred
    size = 30
    d, w, cap, desc = text_path(SEMI, NAME, size, 0, 0, TRACK)
    width = max(w, 64)
    base = 64 + 16 + cap
    d, w, cap, desc = text_path(SEMI, NAME, size, (width - w) / 2, base, TRACK)
    write(f"groundskeeper-lockup-stacked-on-{ground}.svg",
          svg(width, base + desc, tile(x=(width - 64) / 2, uid=f"s{ground}") + f'<path fill="{ink}" d="{d}"/>', "Groundskeeper"))

# favicon: no letters. At tab size the GK read as a lone G (too close to Google's), so the favicon is the robot's
# back-and-forth mowing path on the turf tile, ending at the robot dot.
write("favicon.svg", svg(64, 64,
      f'<rect width="64" height="64" rx="14" fill="{TURF}"/>'
      f'<path d="M8 50V18h12v32h12V18h12v32h12V14" fill="none" stroke="{GRASS}" stroke-width="6" '
      f'stroke-linejoin="round" stroke-linecap="round"/><circle cx="56" cy="14" r="7" fill="{ROBOT}"/>', "Groundskeeper"))

# social card 1200x630
d, w, cap, desc = text_path(SEMI, NAME, 112, 96 + 184 + 48, 0, TRACK)
base = 315 - 92 + cap + 14
d, w, cap, desc = text_path(SEMI, NAME, 112, 96 + 184 + 48, base, TRACK)
d2, w2, cap2, _ = text_path(MED, "A self-hosted recorder, map and", 34, 96 + 184 + 48, base + 70, 0.0)
d3, w3, _, _ = text_path(MED, "guarded control panel for Yarbo.", 34, 96 + 184 + 48, base + 70 + 46, 0.0)
write("groundskeeper-social-card.svg",
      svg(1200, 630, f'<rect width="1200" height="630" fill="{BG_DARK}"/>' + tile(x=96, y=315 - 92, scale=184 / 64, uid="sc")
          + f'<path fill="{INK_DARK_BG}" d="{d}"/><path fill="{MUTED_DARK}" d="{d2} {d3}"/>', "Groundskeeper"))
print("card text right edge", round(96 + 184 + 48 + max(w, w2, w3)), "of 1200")


# ---------------------------------------------------------------- contrast
def lum(hexv):
    h = hexv.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def cr(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


for label, a, b in [("GK on turf", INK_DARK_BG, TURF), ("grass on turf", GRASS, TURF), ("back grass on turf", GRASS_BACK, TURF), ("dot on turf", ROBOT, TURF),
                    ("dot on back grass", ROBOT, GRASS_BACK), ("deep grass on light bg", GRASS_DEEP, BG_LIGHT), ("G on dark bg", CLIPPING, BG_DARK), ("dot on dark bg", ROBOT, BG_DARK),
                    ("deep G on light bg", CLIPPING_DEEP, BG_LIGHT), ("deep dot on light bg", ROBOT_DEEP, BG_LIGHT),
                    ("turf on light bg", TURF, BG_LIGHT), ("turf on dark bg", TURF, BG_DARK),
                    ("ink on dark", INK_DARK_BG, BG_DARK), ("ink on light", INK_LIGHT_BG, BG_LIGHT),
                    ("muted on dark", MUTED_DARK, BG_DARK), ("muted on light", MUTED_LIGHT, BG_LIGHT),
                    ("on-accent dark text on clipping", BG_DARK, CLIPPING), ("white on clipping-deep", "#FFFFFF", CLIPPING_DEEP)]:
    print(f"{label:34s} {cr(a, b):5.2f}")
