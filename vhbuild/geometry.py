"""Meshes: the cover for each machine, the ports, and the assembled device.

Built with Manifold (exact, always-watertight booleans) from the same cut
list the OpenSCAD source uses, so a slicer or a laser cutter gets a clean
part and the 3D viewer shows the real cover, not an approximation of it.

Outputs:
    shell.stl, lid.stl         filament printer, one part each, in print orientation
    print_plate.3mf            both on one plate, ready for any slicer
    resin/shell.stl, lid.stl   resin printer: tighter lid fit
    laser/panels.svg, .dxf     six finger-jointed panels, kerf allowed for
    assembly.glb               the finished device: every cover, every port and
                               every part where it goes, one named node each
                               (the viewer animates these by name)
"""
import io
import math

import numpy as np
import trimesh
from manifold3d import JoinType, Manifold, OpType

from .enclosure import BOARD_RAIL, KERF, LID_LIP, PROCESSES, Box, Cut, box, fits
from .engine import Design

SEGMENTS = 48
FINGER = 12.0        # target finger width on the laser panels
SHEET_GAP = 4.0      # between panels on the cut sheet
SHEET_MARGIN = 2.0   # around the edge of the sheet
FRONT = 25.0         # the cut sheet lies this far in front of the box in the model
COLORS = {                       # RGBA, by catalog category or cover
    "shell": (236, 232, 222, 255), "lid": (236, 232, 222, 150),
    "resin": (150, 162, 180, 255), "resin_lid": (150, 162, 180, 150),
    "wood": (214, 178, 128, 255), "wood_lid": (214, 178, 128, 170),
    "board": (32, 110, 60, 255), "module": (190, 190, 196, 255),
    "sensor": (38, 92, 170, 255), "display": (25, 25, 30, 255),
    "output": (200, 60, 50, 255), "actuator": (60, 60, 70, 255),
    "input": (230, 170, 40, 255), "power": (40, 40, 40, 255),
    "port": (120, 122, 128, 255), "gland": (28, 28, 30, 255), "switch": (25, 25, 25, 255),
}
PANELS = ("bottom", "front", "back", "left", "right", "top")
# Lays each laser panel flat: (Euler degrees for Manifold, quaternion x y z w for three.js).
_S = math.sqrt(0.5)
FLAT = {"bottom": ((0, 0, 0), (0, 0, 0, 1)), "top": ((0, 0, 0), (0, 0, 0, 1)),
        "front": ((90, 0, 0), (_S, 0, 0, _S)), "back": ((90, 0, 0), (_S, 0, 0, _S)),
        "left": ((0, -90, 0), (0, -_S, 0, _S)), "right": ((0, -90, 0), (0, -_S, 0, _S))}


def _mesh(m: Manifold) -> trimesh.Trimesh:
    out = m.to_mesh()
    return trimesh.Trimesh(vertices=np.asarray(out.vert_properties)[:, :3],
                           faces=np.asarray(out.tri_verts), process=False)


def _cube(at, size) -> Manifold:
    return Manifold.cube(list(size)).translate(list(at))


def _cyl(d: float, h: float) -> Manifold:
    return Manifold.cylinder(h, d / 2, d / 2, SEGMENTS)


def _xcyl(d: float, x0: float, x1: float, y: float, z: float) -> Manifold:
    """A cylinder along x from x0 to x1."""
    return _cyl(d, x1 - x0).rotate([0, 90, 0]).translate([x0, y, z])


def _shell_cut(c: Cut, wall: float) -> Manifold:
    x, y, z = c.at
    if c.kind == "box":
        sx, sy, sz = c.size
        g = [1 if abs(s - wall) < 0.01 else 0 for s in (sx, sy, sz)]   # overshoot through the wall
        return _cube((x - g[0], y - g[1], z - g[2]), (sx + 2 * g[0], sy + 2 * g[1], sz + 2 * g[2]))
    if c.axis == "x":
        return _xcyl(c.d, x - 1, x + c.h + 1, y, z)
    return _cyl(c.d, c.h + 2).translate([x, y, z - 1])


def _lid_cut(c: Cut, wall: float) -> Manifold:
    """A lid cut in the assembled frame (the laser top is not flipped)."""
    x, y, z = c.at
    if c.kind == "box":
        return _cube((x, y, z - 1), (c.size[0], c.size[1], wall + 2))
    return _cyl(c.d, wall + 2).translate([x, y, z - 1])


# --- printed covers ----------------------------------------------------------

def shell(b: Box) -> Manifold:
    L, W, H = b.inner
    Lo, Wo, Ho = b.outer
    w = b.wall
    m = _cube((0, 0, 0), (Lo, Wo, Ho)) - _cube((w, w, w), (L, W, H + 1))
    for c in b.cuts:
        if c.target == "shell":
            m = m - _shell_cut(c, w)
    board = b.placed[0]
    for y in (board.y + 1, board.y + board.d - 3):
        m = m + _cube((w + board.x + 2, w + y, w), (board.w - 4, 2, BOARD_RAIL))
    return m


def lid(b: Box) -> Manifold:
    """In print orientation: plate on the bed, lip up, cutouts mirrored in y."""
    L, W, _ = b.inner
    Lo, Wo, _ = b.outer
    w, fit = b.wall, b.fit
    lip_outer = _cube((w + fit, w + fit, w), (L - 2 * fit, W - 2 * fit, LID_LIP))
    lip_inner = _cube((w + fit + 1.2, w + fit + 1.2, w - 1),
                      (L - 2 * fit - 2.4, W - 2 * fit - 2.4, LID_LIP + 2))
    m = _cube((0, 0, 0), (Lo, Wo, w)) + (lip_outer - lip_inner)
    for c in b.cuts:
        if c.target != "lid":
            continue
        x, y, _ = c.at
        if c.kind == "box":
            sx, sy, _ = c.size
            m = m - _cube((x, Wo - y - sy, -1), (sx, sy, w + 2))
        else:
            m = m - _cyl(c.d, w + 2).translate([x, Wo - y, -1])
    return m


def lid_in_place(b: Box, m: Manifold) -> Manifold:
    """Flip the printed lid over onto the box (a rotation, so no mirror image)."""
    _, Wo, Ho = b.outer
    return m.rotate([180, 0, 0]).translate([0, Wo, Ho + b.wall])


# --- laser-cut cover ---------------------------------------------------------

def laser_panels(b: Box) -> dict[str, Manifold]:
    """Six finger-jointed panels, assembled, in the laser box's own frame.

    Every panel starts as a full slab; where two slabs overlap along an edge,
    the overlap is split into an odd number of fingers that go alternately to
    one panel and the other. The corner cubes belong to the bottom and the
    top, so the walls interlock with both and the top can lift straight off.
    """
    T = b.wall
    Lo, Wo, Ho = b.outer
    Ht = Ho + T
    span = {"x": Lo, "y": Wo, "z": Ht}
    slabs = {"bottom": ((0, 0, 0), (Lo, Wo, T)), "top": ((0, 0, Ht - T), (Lo, Wo, T)),
             "front": ((0, 0, 0), (Lo, T, Ht)), "back": ((0, Wo - T, 0), (Lo, T, Ht)),
             "left": ((0, 0, 0), (T, Wo, Ht)), "right": ((Lo - T, 0, 0), (T, Wo, Ht))}
    lose: dict[str, list[Manifold]] = {k: [] for k in slabs}

    def edge(first: str, second: str, axis: str, at: dict) -> None:
        """Fingers along `axis`; `at` gives the low corner on the other two axes."""
        k = "xyz".index(axis)
        length = span[axis] - 2 * T
        n = max(3, int(length // FINGER) | 1)
        step = length / n
        for i in range(n):
            lo = [at.get("x", 0.0), at.get("y", 0.0), at.get("z", 0.0)]
            size = [T, T, T]
            lo[k], size[k] = T + i * step, step
            lose[second if i % 2 == 0 else first].append(_cube(lo, size))

    for face, z in (("bottom", 0.0), ("top", Ht - T)):
        edge(face, "front", "x", {"y": 0.0, "z": z})
        edge(face, "back", "x", {"y": Wo - T, "z": z})
        edge(face, "left", "y", {"x": 0.0, "z": z})
        edge(face, "right", "y", {"x": Lo - T, "z": z})
    for side, y in (("front", 0.0), ("back", Wo - T)):
        edge(side, "left", "z", {"x": 0.0, "y": y})
        edge(side, "right", "z", {"x": Lo - T, "y": y})
    for x, end in ((0.0, "left"), (Lo - T, "right")):
        for y, side in ((0.0, "front"), (Wo - T, "back")):
            for z in (0.0, Ht - T):
                corner = _cube((x, y, z), (T, T, T))
                lose[end].append(corner)
                lose[side].append(corner)

    shell_cuts = [_shell_cut(c, T) for c in b.cuts if c.target == "shell"]
    lid_cuts = [_lid_cut(c, T) for c in b.cuts if c.target == "lid"]
    out = {}
    for name, (at, size) in slabs.items():
        m = _cube(at, size)
        holes = lose[name] + (lid_cuts if name == "top" else shell_cuts)
        if holes:
            m = m - Manifold.batch_boolean(holes, OpType.Add)
        out[name] = m
    return out


def _qmul(a, b):
    """Hamilton product of quaternions (x, y, z, w): rotate by b, then by a."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def _sheet_layout(panels: dict[str, Manifold], width: float) -> dict[str, tuple]:
    """Shelf-pack the flattened panels, long side across.

    name -> (u, v, w, h, flat manifold at the origin, quaternion that laid it flat).
    """
    flat = {}
    for name in PANELS:
        euler, q = FLAT[name]
        m = panels[name].rotate(list(euler))
        x0, y0, z0, x1, y1, z1 = m.bounding_box()
        if y1 - y0 > x1 - x0:                       # portrait: turn it a quarter
            m, q = m.rotate([0, 0, 90]), _qmul((0, 0, _S, _S), q)
            x0, y0, z0, x1, y1, z1 = m.bounding_box()
        flat[name] = (m.translate([-x0, -y0, -z0]), x1 - x0, y1 - y0, q)
    width = max(width, *(f[1] for f in flat.values()))
    out, u, v, row_h = {}, 0.0, 0.0, 0.0
    for name in sorted(PANELS, key=lambda n: -flat[n][2]):
        m, w, h, q = flat[name]
        if u > 0 and u + w > width:
            u, v, row_h = 0.0, v + row_h + SHEET_GAP, 0.0
        out[name] = (u, v, w, h, m, q)
        u += w + SHEET_GAP
        row_h = max(row_h, h)
    return out


def laser_sheet(b: Box, panels: dict[str, Manifold]) -> dict:
    """Cut files and the flat poses the viewer animates from."""
    width = PROCESSES["laser"].bed[0] - 2 * SHEET_MARGIN
    lay = _sheet_layout(panels, width)
    sheet_w = max(u + w for u, v, w, h, *_ in lay.values()) + 2 * SHEET_MARGIN
    sheet_h = max(v + h for u, v, w, h, *_ in lay.values()) + 2 * SHEET_MARGIN
    paths, dxf, area = [], [], 0.0
    for name, (u, v, w, h, m, _) in lay.items():
        cs = m.project().offset(KERF / 2, JoinType.Miter)
        area += cs.area()
        d = []
        for poly in cs.to_polygons():
            pts = [(SHEET_MARGIN + u + px, sheet_h - (SHEET_MARGIN + v + py)) for px, py in poly]   # SVG y runs down
            d.append("M" + " L".join(f"{x:.3f},{y:.3f}" for x, y in pts) + " Z")
            dxf += ["0", "POLYLINE", "8", name, "66", "1", "70", "1"]
            for x, y in pts:
                dxf += ["0", "VERTEX", "8", name, "10", f"{x:.3f}", "20", f"{sheet_h - y:.3f}"]
            dxf += ["0", "SEQEND"]
        paths.append(f'<g id="{name}"><path d="{" ".join(d)}"/></g>')
    svg = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<svg xmlns="http://www.w3.org/2000/svg" width="{sheet_w:.2f}mm" height="{sheet_h:.2f}mm" '
           f'viewBox="0 0 {sheet_w:.3f} {sheet_h:.3f}">\n'
           f'<!-- V-Hbuild laser panels: cut every path (red, hairline). 3 mm sheet, '
           f'kerf {KERF} mm already allowed for. -->\n'
           f'<g fill="none" stroke="#ff0000" stroke-width="0.1">\n' + "\n".join(paths) + "\n</g>\n</svg>\n")
    dxf_text = "\n".join(["0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
                          "0", "SECTION", "2", "ENTITIES", *dxf, "0", "ENDSEC", "0", "EOF"]) + "\n"

    T, s = b.wall, b.shift
    poses = {}
    for name, (u, v, w, h, _, q) in lay.items():
        x0, y0, z0, x1, y1, z1 = panels[name].bounding_box()
        poses[f"laser_{name}"] = {
            "center": [round((x0 + x1) / 2 + s, 3), round((y0 + y1) / 2 + s, 3), round((z0 + z1) / 2 + s, 3)],
            "flat_center": [round(u + w / 2, 3), round(-FRONT - v - h / 2, 3), round(T / 2, 3)],
            "quat": [round(c, 6) for c in q]}
    return {"svg": svg.encode(), "dxf": dxf_text.encode(), "poses": poses,
            "sheet_mm": [round(sheet_w, 1), round(sheet_h, 1)], "area_cm2": round(area / 100, 1),
            "flat_box": [[0, round(-FRONT - sheet_h, 1)], [round(sheet_w, 1), round(-FRONT, 1)]]}


# --- ports and parts ---------------------------------------------------------

def _port_meshes(b: Box) -> dict[str, tuple[trimesh.Trimesh, str]]:
    """The panel connectors, through the connector wall, in the model frame."""
    out, w = {}, b.wall
    for p in b.ports:
        X, y, z = b.port_at(p)
        if p.kind == "gx12":
            m = (_xcyl(11, X - w - 17, X - w, y, z) + _xcyl(16, X, X + 2.5, y, z)
                 + _xcyl(12, X, X + 8, y, z) + _xcyl(13, X + 8, X + 30, y, z))
            key = "port"
        elif p.kind == "gland":
            m = _xcyl(15, X - w - 4, X - w, y, z) + _xcyl(15, X, X + 4, y, z) + _xcyl(11, X + 4, X + 12, y, z)
            key = "gland"
        else:
            m = (_cube((X - w - 18, y - 6.5, z - 4.25), (18, 13, 8.5))
                 + _cube((X, y - 7.5, z - 5), (2.5, 15, 10)) + _cube((X + 2.5, y - 5.5, z - 3.25), (1.5, 11, 6.5)))
            key = "switch"
        out[p.ref] = (_mesh(m), key)
    return out


def _part_meshes(d: Design, b: Box) -> dict[str, tuple[trimesh.Trimesh, str]]:
    """ref -> (mesh, colour key) for everything inside the box, plus what lives outside."""
    out = {}
    w = b.wall
    for p in b.placed:
        x, y, z = w + p.x, w + p.y, b.z_of(p)
        if p.ref == "U1":
            pcb = _cube((x, y, z), (p.w, p.d, 1.6))
            can = _cube((x + p.w * 0.45, y + p.d * 0.2, z + 1.6), (p.w * 0.45, p.d * 0.6, 3))
            usb = _cube((x - 0.5, y + p.d / 2 - 4, z + 1.6), (7, 8, 3))
            out["U1"] = (_mesh(pcb + can + usb), "board")
            continue
        pcb = _cube((x, y, z), (p.w, p.d, 1.6))
        body_h = max(p.h - 1.6, 0.8)
        if p.on_lid:   # component side faces the lid window
            body = _cube((x + 2, y + 2, z + 1.6), (p.w - 4, p.d - 4, body_h))
        elif p.panel == "usb":   # a charger: its socket at the wall
            body = (_cube((x + p.w * 0.3, y + p.d * 0.15, z + 1.6), (p.w * 0.6, p.d * 0.7, body_h))
                    + _cube((x - 0.5, y + p.d / 2 - 4.5, z + 1.6), (7.5, 9, 3.2)))
        else:
            body = _cube((x + p.w * 0.15, y + p.d * 0.15, z + 1.6), (p.w * 0.7, p.d * 0.7, body_h))
        out[p.ref] = (_mesh(pcb + body), p.category if p.category in COLORS else "module")

    # Probes, pumps and the like sit outside, beside the connector wall.
    for p in b.outside:
        out[p.ref] = (_mesh(_cube((w + p.x, w + p.y, 0), (p.w, p.d, p.h))),
                      p.category if p.category in COLORS else "module")
    out.update(_port_meshes(b))
    return out


def _colored(mesh: trimesh.Trimesh, key: str) -> trimesh.Trimesh:
    """One flat PBR material per part - the viewer recolours and fades by node."""
    mesh = mesh.copy()
    rgba = COLORS[key]
    material = trimesh.visual.material.PBRMaterial(
        name=key, baseColorFactor=rgba, metallicFactor=0.0, roughnessFactor=0.8,
        alphaMode="BLEND" if rgba[3] < 255 else "OPAQUE", doubleSided=True)
    mesh.visual = trimesh.visual.TextureVisuals(material=material)
    return mesh


def _cover_info(b: Box, **extra) -> dict:
    ok, why = fits(b)
    return {"outer": [round(v, 1) for v in b.outer], "fits": ok, "fit_note": why, **extra}


def _sheet_note(sheet_mm: list) -> str:
    bx, by, _ = PROCESSES["laser"].bed
    w, h = sheet_mm
    if (w <= bx and h <= by) or (w <= by and h <= bx):
        return f"All six panels fit one {bx} x {by} mm sheet."
    return (f"The layout is {w:.0f} x {h:.0f} mm, more than one {bx} x {by} mm sheet: "
            "cut it in two passes, or rearrange the panels in your laser software.")


def build(d: Design) -> dict:
    """All mesh and cut outputs as bytes, plus the numbers the viewer and manifest need."""
    b = box(d, "fdm")
    rb = box(d, "resin")
    lb = box(d, "laser")
    sh, ld = shell(b), lid(b)
    rsh, rld = shell(rb), lid(rb)
    panels = laser_panels(lb)
    sheet = laser_sheet(lb, panels)
    shell_mesh, lid_mesh = _mesh(sh), _mesh(ld)
    rshell_mesh, rlid_mesh = _mesh(rsh), _mesh(rld)

    plate = trimesh.Scene()
    plate.add_geometry(shell_mesh, node_name="shell", geom_name="shell")
    plate.add_geometry(lid_mesh, node_name="lid", geom_name="lid",
                       transform=trimesh.transformations.translation_matrix([b.outer[0] + 10, 0, 0]))

    scene = trimesh.Scene()
    scene.add_geometry(_colored(shell_mesh, "shell"), node_name="shell", geom_name="shell")
    scene.add_geometry(_colored(_mesh(lid_in_place(b, ld)), "lid"), node_name="lid", geom_name="lid")
    scene.add_geometry(_colored(rshell_mesh, "resin"), node_name="resin_shell", geom_name="resin_shell")
    scene.add_geometry(_colored(_mesh(lid_in_place(rb, rld)), "resin_lid"),
                       node_name="resin_lid", geom_name="resin_lid")
    s = lb.shift
    for name, m in panels.items():
        key = "wood_lid" if name == "top" else "wood"
        scene.add_geometry(_colored(_mesh(m.translate([s, s, s])), key),
                           node_name=f"laser_{name}", geom_name=f"laser_{name}")
    for ref, (mesh, key) in _part_meshes(d, b).items():
        scene.add_geometry(_colored(mesh, key), node_name=ref, geom_name=ref)

    printed = (sh.volume() + ld.volume()) / 1000
    resin_ml = (rsh.volume() + rld.volume()) / 1000
    return {
        "shell.stl": shell_mesh.export(file_type="stl"),
        "lid.stl": lid_mesh.export(file_type="stl"),
        "print_plate.3mf": plate.export(file_type="3mf"),
        "resin/shell.stl": rshell_mesh.export(file_type="stl"),
        "resin/lid.stl": rlid_mesh.export(file_type="stl"),
        "laser/panels.svg": sheet["svg"],
        "laser/panels.dxf": sheet["dxf"],
        "assembly.glb": scene.export(file_type="glb"),
        "box": b,
        "volume_cm3": round(printed, 1),
        "covers": {
            "fdm": _cover_info(b, volume_cm3=round(printed, 1)),
            "resin": _cover_info(rb, resin_ml=round(resin_ml, 1)),
            "laser": _cover_info(lb, sheet_note=_sheet_note(sheet["sheet_mm"]), sheet_mm=sheet["sheet_mm"], area_cm2=sheet["area_cm2"],
                                 poses=sheet["poses"], flat_box=sheet["flat_box"],
                                 panels=len(panels)),
        },
        "watertight": all(m.is_watertight for m in (shell_mesh, lid_mesh, rshell_mesh, rlid_mesh)),
    }


def load_glb(data: bytes) -> trimesh.Scene:
    return trimesh.load(io.BytesIO(data), file_type="glb")
