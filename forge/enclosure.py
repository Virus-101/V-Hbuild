"""A printable box for the design.

Parts are packed in rows (board first, against the wall its USB port faces),
the box is sized around them, and each part's catalog `panel` says what it
needs from the shell: a window for a display, a dome hole for a PIR sensor,
vents for air sensors, a cable hole for probes that live outside.

The cutouts are described once, as data, in the assembled frame (origin at
the outer bottom corner of the box, z up). The OpenSCAD source and the meshes
(geometry.py) are both generated from that one list, so they cannot disagree.

The lid is printed plate-down and flipped onto the box, which mirrors it in
y. Lid cutouts are therefore mirrored when the lid is built, so after the
flip each hole sits over its part.
"""
from dataclasses import dataclass

from .engine import Design

WALL = 2.0
GAP = 4.0            # between parts, and between parts and walls
WIRING_ROOM = 10.0   # headroom for jumper wires
MAX_ROW = 100.0      # inner length before starting a new row
BOARD_RAIL = 3.0     # the board rests on rails this tall
LID_LIP = 3.0
FIT = 0.3            # clearance between the lid lip and the walls
OUTSIDE = {"soil-capacitive", "ds18b20", "pump-5v", "psu-5v2a"}   # live outside the box
LID_PANELS = {"window", "dome", "twin_holes", "button", "slot"}   # mounted under the lid


@dataclass
class Placed:
    ref: str
    label: str
    panel: str
    x: float            # inner-frame position of the footprint
    y: float
    w: float
    d: float
    h: float
    category: str = ""

    @property
    def on_lid(self) -> bool:
        return self.panel in LID_PANELS


@dataclass
class Cut:
    target: str          # "shell" | "lid"
    kind: str            # "box" | "cyl"
    at: tuple            # box: min corner (x, y, z); cyl: centre (x, y, z)
    size: tuple = ()     # box: (sx, sy, sz)
    d: float = 0.0       # cyl diameter
    h: float = 0.0       # cyl length
    axis: str = "z"      # cyl axis
    note: str = ""


@dataclass
class Box:
    placed: list
    inner: tuple         # (L, W, H)
    cuts: list

    @property
    def outer(self) -> tuple:
        L, W, H = self.inner
        return (L + 2 * WALL, W + 2 * WALL, H + WALL)

    def z_of(self, p: Placed) -> float:
        """Assembled-frame z of the underside of a placed part."""
        if p.ref == "U1":
            return WALL + BOARD_RAIL
        if p.on_lid:
            return WALL + self.inner[2] - p.h
        return WALL


def layout(d: Design) -> tuple[list[Placed], tuple[float, float, float]]:
    items = [("U1", d.board.name, "usb", *d.board.size_mm, "board")]
    for i in d.instances:
        if i.part.id in OUTSIDE or not any(i.part.size_mm):
            continue
        w, dd, h = i.part.size_mm
        items.append((i.ref, i.part.name, i.part.panel, max(w, dd), min(w, dd), h, i.part.category))
    board, rest = items[0], sorted(items[1:], key=lambda t: -t[3])

    placed, x, y, row_depth = [], GAP, GAP, 0.0
    for ref, label, panel, w, dd, h, cat in [board] + rest:
        if x + w > MAX_ROW and x > GAP:
            x, y, row_depth = GAP, y + row_depth + GAP, 0.0
        # The board's USB end sits against the x=0 wall so the cable can reach it.
        px = 0.0 if ref == "U1" else x
        placed.append(Placed(ref, label, panel, px, y, w, dd, h, cat))
        x = px + w + GAP
        row_depth = max(row_depth, dd)
    inner_l = max(p.x + p.w for p in placed) + GAP
    inner_w = max(p.y + p.d for p in placed) + GAP
    floor_h = max(p.h + (BOARD_RAIL if p.ref == "U1" else 0) for p in placed if not p.on_lid)
    lid_h = max((p.h for p in placed if p.on_lid), default=0)
    inner_h = max(20.0, floor_h + lid_h + WIRING_ROOM)
    return placed, (round(inner_l, 1), round(inner_w, 1), round(inner_h, 1))


def box(d: Design) -> Box:
    placed, (L, W, H) = layout(d)
    top = WALL + H
    cuts: list[Cut] = []
    for p in placed:
        ox, oy = WALL + p.x, WALL + p.y             # outer frame
        cx, cy = ox + p.w / 2, oy + p.d / 2
        tag = f"{p.ref} {p.label}"
        if p.panel == "window":
            cuts.append(Cut("lid", "box", (ox + 2, oy + 2, top), (p.w - 4, p.d - 4, WALL), note=tag))
        elif p.panel == "dome":
            cuts.append(Cut("lid", "cyl", (cx, cy, top), d=23.5, h=WALL, note=tag))
        elif p.panel == "twin_holes":
            for dx in (-13, 13):
                cuts.append(Cut("lid", "cyl", (cx + dx, cy, top), d=16.5, h=WALL, note=tag))
        elif p.panel == "button":
            cuts.append(Cut("lid", "cyl", (cx, cy, top), d=12.5, h=WALL, note=tag))
        elif p.panel == "slot":
            cuts.append(Cut("lid", "box", (cx - 11.75, cy - 6.25, top), (23.5, 12.5, WALL),
                            note=f"{tag} (servo body passes through)"))
        elif p.panel == "vent":
            for i in range(4):
                cuts.append(Cut("shell", "box", (ox + i * 5, WALL + W, WALL + 4), (2.5, WALL, 10),
                                note=f"{tag} vent"))
        elif p.panel == "usb":
            cuts.append(Cut("shell", "box", (0, cy - 6, WALL + 2), (WALL, 12, 8), note=f"{tag} USB"))

    cables = sum(1 for i in d.instances
                 if (i.part.panel == "cable" or i.part.id in OUTSIDE) and i.part.id != "psu-5v2a")
    per_row = max(1, int(W // 16))
    for n in range(cables):
        # Spread along the far wall; a second row once the first is full.
        row, col = divmod(n, per_row)
        in_row = min(cables - row * per_row, per_row)
        cy = WALL + W * (col + 1) / (in_row + 1)
        cuts.append(Cut("shell", "cyl", (WALL + L, cy, WALL + 8 + row * 15), d=12.5, h=WALL,
                        axis="x", note="cable gland (PG7)"))
    return Box(placed, (L, W, H), cuts)


def _scad_cut(c: Cut, lid_frame: bool) -> str:
    # Every cut overshoots by 1 mm through the wall so the boolean leaves no skin.
    x, y, z = c.at
    if c.kind == "box":
        sx, sy, sz = c.size
        if lid_frame:
            return f"translate([{x:.2f}, {y:.2f}, -1]) cube([{sx:.2f}, {sy:.2f}, WALL + 2]); // {c.note}"
        g = [1 if s <= WALL + 0.01 else 0 for s in (sx, sy, sz)]
        return (f"translate([{x - g[0]:.2f}, {y - g[1]:.2f}, {z - g[2]:.2f}]) "
                f"cube([{sx + 2 * g[0]:.2f}, {sy + 2 * g[1]:.2f}, {sz + 2 * g[2]:.2f}]); // {c.note}")
    if lid_frame or c.axis == "z":
        return f"translate([{x:.2f}, {y:.2f}, -1]) cylinder(d = {c.d}, h = WALL + 2); // {c.note}"
    return (f"translate([{x - 1:.2f}, {y:.2f}, {z:.2f}]) rotate([0, 90, 0]) "
            f"cylinder(d = {c.d}, h = {c.h + 2}); // {c.note}")


def scad(d: Design) -> str:
    b = box(d)
    L, W, H = b.inner
    board = b.placed[0]
    shell_cuts = "\n".join("    " + _scad_cut(c, False) for c in b.cuts if c.target == "shell")
    lid_cuts = "\n".join("      " + _scad_cut(c, True) for c in b.cuts if c.target == "lid")
    floor = "\n".join(f"// {p.ref:4} at ({p.x:5.1f}, {p.y:5.1f})  {p.w:.0f} x {p.d:.0f} mm  "
                      f"{'under the lid' if p.on_lid else 'on the floor'}  {p.label}"
                      for p in b.placed)
    return f"""// {d.name} - enclosure generated by Forge.
// Open in OpenSCAD, press F6, export STL - or use the ready STL/3MF files next to this one.
// The lid is modelled plate-down, as printed; its cutouts are mirrored so they line up
// after it is flipped onto the box.
// Part positions (mm from the inside corner):
{floor}

WALL = {WALL};
INNER_L = {L};
INNER_W = {W};
INNER_H = {H};
LID_LIP = {LID_LIP};
FIT = {FIT};      // clearance for the lid lip; raise it if your printer runs tight
$fn = 48;

module shell() {{
  difference() {{
    cube([INNER_L + 2 * WALL, INNER_W + 2 * WALL, INNER_H + WALL]);
    translate([WALL, WALL, WALL]) cube([INNER_L, INNER_W, INNER_H + 1]);
{shell_cuts}
  }}
  // Two rails to rest the board on, clear of solder joints underneath.
  for (y = [{board.y + 1:.1f}, {board.y + board.d - 3:.1f}])
    translate([WALL + {board.x + 2:.1f}, WALL + y, WALL]) cube([{board.w - 4:.1f}, 2, {BOARD_RAIL}]);
}}

module lid() {{
  difference() {{
    union() {{
      cube([INNER_L + 2 * WALL, INNER_W + 2 * WALL, WALL]);
      translate([WALL + FIT, WALL + FIT, WALL])
        difference() {{
          cube([INNER_L - 2 * FIT, INNER_W - 2 * FIT, LID_LIP]);
          translate([1.2, 1.2, -1]) cube([INNER_L - 2 * FIT - 2.4, INNER_W - 2 * FIT - 2.4, LID_LIP + 2]);
        }}
    }}
    translate([0, INNER_W + 2 * WALL, 0]) mirror([0, 1, 0]) {{
{lid_cuts or "      // nothing on the lid"}
    }}
  }}
}}

shell();
translate([INNER_L + 2 * WALL + 10, 0, 0]) lid();
"""
