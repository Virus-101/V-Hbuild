"""The cover for the design, for three kinds of machine.

Parts are packed in rows (board first, against the wall its USB port faces),
the box is sized around them, and each part's catalog `panel` says what it
needs from the cover: a window for a display, a dome hole for a PIR sensor,
vents for air sensors, a USB opening for the board and the charger.

Everything that has to pass through a wall gets a real panel part, mounted on
the connector wall (the end opposite the USB ports): a GX12 socket for each
probe that lives outside, a PG7 gland for each load cable, the power switch
on battery builds. Those are `ports`, each with its own cut-out.

The same box comes in three versions (`PROCESSES`):

    fdm     shell + lid for a filament printer          2 mm walls, 0.3 mm lid fit
    resin   shell + lid for a resin (MSLA) printer      2 mm walls, 0.15 mm lid fit
    laser   six finger-jointed panels, 3 mm sheet       the top lifts off

All three share one inside: parts sit at the same place in every version, so
the 3D model, the wiring and the steps hold for whichever cover is made. The
laser box has thicker walls, so it is built in its own frame and shifted by
`shift` to line its inside up with the others.

The cutouts are described once, as data, in each cover's frame (origin at the
outer bottom corner, z up). The OpenSCAD source and the meshes (geometry.py)
are both generated from that one list, so they cannot disagree.

The printed lid goes on plate-down and is flipped onto the box, which mirrors
it in y. Its cutouts are therefore mirrored when it is built, so after the
flip each hole sits over its part.
"""
import math
from dataclasses import dataclass, field

from .engine import Design

WALL = 2.0           # printed walls; also the frame the 3D model is drawn in
GAP = 4.0            # between parts, and between parts and walls
WIRING_ROOM = 10.0   # headroom for jumper wires
MAX_ROW = 100.0      # inner length before starting a new row
BOARD_RAIL = 3.0     # the board rests on rails (or standoffs) this tall
LID_LIP = 3.0
FIT = 0.3            # FDM clearance between the lid lip and the walls
PORT_ROOM = 20.0     # clear space behind the connector wall for socket bodies
PORT_PITCH = 18.0    # between neighbouring ports
PORT_ROW = 16.0      # between rows of ports
OUTSIDE = {"soil-capacitive", "ds18b20", "pump-5v", "psu-5v2a"}   # live outside the box
LID_PANELS = {"window", "dome", "twin_holes", "button", "slot"}   # mounted under the lid
WALL_PANELS = {"switch"}                                          # mounted through a wall


@dataclass(frozen=True)
class Process:
    id: str
    label: str
    machine: str         # key under the manifest's `machines`
    wall: float
    fit: float
    bed: tuple           # usable build volume / sheet (x, y, z), mm; z = 0 for a cutter
    material: str
    how: str


PROCESSES = {p.id: p for p in [
    Process("fdm", "3D printer (filament)", "printer", 2.0, FIT, (220, 220, 220),
            "PLA indoors, PETG outdoors",
            "0.2 mm layers, no supports. The lid prints plate-down."),
    Process("resin", "Resin printer (MSLA)", "resin_printer", 2.0, 0.15, (218, 122, 220),
            "tough or ABS-like resin",
            "0.05 mm layers. Tilt each part 30-45 degrees with supports on the outside faces "
            "only, then wash and cure it before fitting anything."),
    Process("laser", "Laser cutter", "laser_cutter", 3.0, 0.0, (300, 200, 0),
            "3 mm plywood or acrylic",
            "Cut panels.svg (or .dxf) at 1:1; kerf is already allowed for. Glue the bottom "
            "and the four walls; the top is a press fit and lifts off."),
]}
KERF = 0.15          # laser beam width the cut paths are offset by


@dataclass(frozen=True)
class Connector:
    name: str            # BOM line
    price: float
    hole: tuple          # ("cyl", d) or ("box", width, height)
    reach: float         # how far it sticks out of the wall (plug or gland dome)


CONNECTORS = {
    "gx12": Connector("GX12 aviation connector, 3-pin panel socket + cable plug", 1.20,
                      ("cyl", 12.2), 30.0),
    "gland": Connector("PG7 cable gland, 3-6.5 mm cable", 0.30, ("cyl", 12.5), 12.0),
    "rocker": Connector("", 0.0, ("box", 13.0, 8.5), 4.0),     # the switch is in the BOM already
}


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
class Port:
    ref: str             # model node: J1, G1, ... or the switch's own ref
    kind: str            # a CONNECTORS key
    serves: str          # the part it connects
    label: str
    y: float             # inner-frame position on the connector wall
    z: float             # height of its centre above the floor's top face

    @property
    def connector(self) -> Connector:
        return CONNECTORS[self.kind]


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
    ports: list = field(default_factory=list)
    outside: list = field(default_factory=list)   # Placed, beside the connector wall
    process: str = "fdm"

    @property
    def wall(self) -> float:
        return PROCESSES[self.process].wall

    @property
    def fit(self) -> float:
        return PROCESSES[self.process].fit

    @property
    def shift(self) -> float:
        """Add to this cover's coordinates to reach the shared model frame."""
        return WALL - self.wall

    @property
    def outer(self) -> tuple:
        L, W, H = self.inner
        return (L + 2 * self.wall, W + 2 * self.wall, H + self.wall)

    def z_of(self, p: Placed) -> float:
        """Assembled-frame z of the underside of a placed part."""
        if p.ref == "U1":
            return self.wall + BOARD_RAIL
        if p.on_lid:
            return self.wall + self.inner[2] - p.h
        return self.wall

    def port_at(self, port: Port) -> tuple:
        """Centre of a port where it meets the outside of the connector wall."""
        return (self.outer[0], self.wall + port.y, self.wall + port.z)


def layout(d: Design) -> tuple[list[Placed], tuple[float, float, float]]:
    items = [("U1", d.board.name, "usb", *d.board.size_mm, "board")]
    for i in d.instances:
        if i.part.id in OUTSIDE or i.part.panel in WALL_PANELS or not any(i.part.size_mm):
            continue
        w, dd, h = i.part.size_mm
        items.append((i.ref, i.part.name, i.part.panel, max(w, dd), min(w, dd), h, i.part.category))
    board = items[0]
    usb = [t for t in items[1:] if t[2] == "usb"]
    rest = sorted((t for t in items[1:] if t[2] != "usb"), key=lambda t: -t[3])

    placed, x, y, row_depth = [], GAP, GAP, 0.0
    for ref, label, panel, w, dd, h, cat in [board] + usb + rest:
        against_wall = panel == "usb"
        if against_wall and placed:
            # Another USB socket (the charger): its own row, against the same wall.
            x, y, row_depth = GAP, y + row_depth + GAP, 0.0
        elif x + w > MAX_ROW and x > GAP:
            x, y, row_depth = GAP, y + row_depth + GAP, 0.0
        # USB ends sit against the x=0 wall so a cable can reach them.
        px = 0.0 if against_wall else x
        placed.append(Placed(ref, label, panel, px, y, w, dd, h, cat))
        x = px + w + GAP
        row_depth = max(row_depth, dd)
    inner_l = max(p.x + p.w for p in placed) + GAP
    inner_w = max(p.y + p.d for p in placed) + GAP
    floor_h = max(p.h + (BOARD_RAIL if p.ref == "U1" else 0) for p in placed if not p.on_lid)
    lid_h = max((p.h for p in placed if p.on_lid), default=0)
    inner_h = max(20.0, floor_h + lid_h + WIRING_ROOM)
    return placed, (round(inner_l, 1), round(inner_w, 1), round(inner_h, 1))


def _port_list(d: Design) -> list[tuple[str, str, str, str]]:
    """(ref, kind, serves, label) for everything that passes through the connector wall."""
    out, n_j, n_g = [], 0, 0
    for i in d.instances:
        p = i.part
        if p.panel == "switch":
            out.append((i.ref, "rocker", i.ref, "power switch"))
        elif p.id in OUTSIDE and p.id != "psu-5v2a":
            if p.category == "sensor":      # probes unplug, so the box can come indoors
                n_j += 1
                out.append((f"J{n_j}", "gx12", i.ref, f"socket for {i.ref} ({p.name.split(' (')[0]})"))
            else:
                n_g += 1
                out.append((f"G{n_g}", "gland", i.ref, f"gland for the {i.ref} cable"))
        elif p.panel == "cable" and not d.has("pump-5v"):   # a relay switching something else
            n_g += 1
            out.append((f"G{n_g}", "gland", i.ref, f"gland for the cable {i.ref} switches"))
    return out


def box(d: Design, process: str = "fdm") -> Box:
    if process not in PROCESSES:
        raise ValueError(f"Unknown cover process: {process}")
    w = PROCESSES[process].wall
    placed, (L, W, H) = layout(d)
    specs = _port_list(d)

    # Ports go in rows along the connector wall, below the lid lip; the box
    # grows to make room for them, never the other way round.
    ports: list[Port] = []
    if specs:
        L = round(L + PORT_ROOM, 1)
        rows = max(1, int((H - LID_LIP - 4) // PORT_ROW))
        per_row = math.ceil(len(specs) / rows)
        W = max(W, round(per_row * PORT_PITCH, 1))
        for n, (ref, kind, serves, label) in enumerate(specs):
            row, col = divmod(n, per_row)
            in_row = min(len(specs) - row * per_row, per_row)
            ports.append(Port(ref, kind, serves, label,
                              y=round(W * (col + 1) / (in_row + 1), 2),
                              z=round(9 + row * PORT_ROW, 2)))

    top = w + H
    cuts: list[Cut] = []
    b = Box(placed, (L, W, H), cuts, ports, [], process)
    for p in placed:
        ox, oy = w + p.x, w + p.y             # outer frame
        cx, cy = ox + p.w / 2, oy + p.d / 2
        tag = f"{p.ref} {p.label}"
        if p.panel == "window":
            cuts.append(Cut("lid", "box", (ox + 2, oy + 2, top), (p.w - 4, p.d - 4, w), note=tag))
        elif p.panel == "dome":
            cuts.append(Cut("lid", "cyl", (cx, cy, top), d=23.5, h=w, note=tag))
        elif p.panel == "twin_holes":
            for dx in (-13, 13):
                cuts.append(Cut("lid", "cyl", (cx + dx, cy, top), d=16.5, h=w, note=tag))
        elif p.panel == "button":
            cuts.append(Cut("lid", "cyl", (cx, cy, top), d=12.5, h=w, note=tag))
        elif p.panel == "slot":
            cuts.append(Cut("lid", "box", (cx - 11.75, cy - 6.25, top), (23.5, 12.5, w),
                            note=f"{tag} (servo body passes through)"))
        elif p.panel == "vent":
            for i in range(4):
                cuts.append(Cut("shell", "box", (ox + i * 5, w + W, w + 4), (2.5, w, 10),
                                note=f"{tag} vent"))
        elif p.panel == "usb":
            z0 = max(w + 0.5, b.z_of(p) - 2)
            cuts.append(Cut("shell", "box", (0, cy - 6, z0), (w, 12, 8), note=f"{tag} USB"))

    for port in ports:
        x, y, z = b.port_at(port)
        hole = port.connector.hole
        note = f"{port.ref} {port.label}"
        if hole[0] == "cyl":
            cuts.append(Cut("shell", "cyl", (x - w, y, z), d=hole[1], h=w, axis="x", note=note))
        else:
            cuts.append(Cut("shell", "box", (x - w, y - hole[1] / 2, z - hole[2] / 2),
                            (w, hole[1], hole[2]), note=note))

    # What lives outside lines up beside the connector wall, past the plugs.
    Lo = b.outer[0]
    reach = max((pt.connector.reach for pt in ports), default=10)
    y = 0.0
    for i in d.instances:
        if i.part.id not in OUTSIDE or i.part.id == "psu-5v2a":
            continue
        sw, sd, sh = i.part.size_mm
        b.outside.append(Placed(i.ref, i.part.name, i.part.panel,
                                Lo - w + reach + 14, y, max(sw, sd), min(sw, sd), sh, i.part.category))
        y += min(sw, sd) + 12
    return b


def fits(b: Box) -> tuple[bool, str]:
    """Does the cover fit the usual machine of its kind? (bool, one line saying why)"""
    proc = PROCESSES[b.process]
    Lo, Wo, Ho = b.outer
    if proc.id == "laser":
        bx, by, _ = proc.bed
        Ht = Ho + b.wall
        faces = [(Lo, Wo), (Lo, Ht), (Wo, Ht)]
        ok = all(max(f) <= max(bx, by) and min(f) <= min(bx, by) for f in faces)
        big = max(faces, key=lambda f: f[0] * f[1])
        return ok, (f"Largest panel {big[0]:.0f} x {big[1]:.0f} mm; "
                    + ("fits" if ok else "too big for") + f" a {bx} x {by} mm bed.")
    part = sorted((Lo, Wo, Ho + b.wall), reverse=True)
    bed = sorted(proc.bed, reverse=True)
    ok = all(p <= q for p, q in zip(part, bed))
    return ok, (f"Shell {Lo:.0f} x {Wo:.0f} x {Ho:.0f} mm; " + ("fits" if ok else "too big for")
                + f" a {proc.bed[0]} x {proc.bed[1]} x {proc.bed[2]} mm build volume.")


def hardware(d: Design) -> list[tuple[str, str, float, str]]:
    """BOM lines for the panel connectors: (ref, name, unit price, why)."""
    return [(ref, CONNECTORS[kind].name, CONNECTORS[kind].price, label)
            for ref, kind, _, label in _port_list(d) if CONNECTORS[kind].name]


def _scad_cut(c: Cut, lid_frame: bool, wall: float) -> str:
    # Every cut overshoots by 1 mm through the wall so the boolean leaves no skin.
    x, y, z = c.at
    if c.kind == "box":
        sx, sy, sz = c.size
        if lid_frame:
            return f"translate([{x:.2f}, {y:.2f}, -1]) cube([{sx:.2f}, {sy:.2f}, WALL + 2]); // {c.note}"
        g = [1 if abs(s - wall) < 0.01 else 0 for s in (sx, sy, sz)]   # the axis through the wall
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
    shell_cuts = "\n".join("    " + _scad_cut(c, False, b.wall) for c in b.cuts if c.target == "shell")
    lid_cuts = "\n".join("      " + _scad_cut(c, True, b.wall) for c in b.cuts if c.target == "lid")
    floor = "\n".join(f"// {p.ref:4} at ({p.x:5.1f}, {p.y:5.1f})  {p.w:.0f} x {p.d:.0f} mm  "
                      f"{'under the lid' if p.on_lid else 'on the floor'}  {p.label}"
                      for p in b.placed)
    ports = "\n".join(f"// {pt.ref:4} {pt.label}: {pt.connector.name or 'panel switch'}"
                      for pt in b.ports) or "// (none)"
    return f"""// {d.name} - enclosure generated by V-Hbuild.
// Open in OpenSCAD, press F6, export STL - or use the ready STL/3MF files next to this one.
// The lid is modelled plate-down, as printed; its cutouts are mirrored so they line up
// after it is flipped onto the box.
// Part positions (mm from the inside corner):
{floor}
// Ports on the connector wall (the far end from the USB ports):
{ports}

WALL = {b.wall};
INNER_L = {L};
INNER_W = {W};
INNER_H = {H};
LID_LIP = {LID_LIP};
FIT = {b.fit};      // clearance for the lid lip; raise it if your printer runs tight
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
