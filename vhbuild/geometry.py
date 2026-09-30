"""Meshes: the printable enclosure and the assembled device.

Built with Manifold (exact, always-watertight booleans) from the same cut
list the OpenSCAD source uses, so a slicer gets a clean part and the 3D
viewer shows the real box, not an approximation of it.

Outputs:
    shell.stl, lid.stl   one part each, in print orientation
    print_plate.3mf      both parts on one plate, ready for any slicer
    assembly.glb         the finished device: shell, lid on top, every part
                         where it goes, one named node per ref (the viewer
                         animates these by name)
"""
import io

import numpy as np
import trimesh
from manifold3d import Manifold

from .enclosure import BOARD_RAIL, FIT, LID_LIP, OUTSIDE, WALL, Box, Cut, box
from .engine import Design

SEGMENTS = 48
COLORS = {                       # RGBA, by catalog category
    "shell": (236, 232, 222, 255), "lid": (236, 232, 222, 150),
    "board": (32, 110, 60, 255), "module": (190, 190, 196, 255),
    "sensor": (38, 92, 170, 255), "display": (25, 25, 30, 255),
    "output": (200, 60, 50, 255), "actuator": (60, 60, 70, 255),
    "input": (230, 170, 40, 255), "power": (40, 40, 40, 255),
}


def _mesh(m: Manifold) -> trimesh.Trimesh:
    out = m.to_mesh()
    return trimesh.Trimesh(vertices=np.asarray(out.vert_properties)[:, :3],
                           faces=np.asarray(out.tri_verts), process=False)


def _cube(at, size) -> Manifold:
    return Manifold.cube(list(size)).translate(list(at))


def _cyl(d: float, h: float) -> Manifold:
    return Manifold.cylinder(h, d / 2, d / 2, SEGMENTS)


def _shell_cut(c: Cut) -> Manifold:
    x, y, z = c.at
    if c.kind == "box":
        sx, sy, sz = c.size
        g = [1 if s <= WALL + 0.01 else 0 for s in (sx, sy, sz)]   # overshoot through the wall
        return _cube((x - g[0], y - g[1], z - g[2]), (sx + 2 * g[0], sy + 2 * g[1], sz + 2 * g[2]))
    if c.axis == "x":
        return _cyl(c.d, c.h + 2).rotate([0, 90, 0]).translate([x - 1, y, z])
    return _cyl(c.d, c.h + 2).translate([x, y, z - 1])


def shell(b: Box) -> Manifold:
    L, W, H = b.inner
    Lo, Wo, Ho = b.outer
    m = _cube((0, 0, 0), (Lo, Wo, Ho)) - _cube((WALL, WALL, WALL), (L, W, H + 1))
    for c in b.cuts:
        if c.target == "shell":
            m = m - _shell_cut(c)
    board = b.placed[0]
    for y in (board.y + 1, board.y + board.d - 3):
        m = m + _cube((WALL + board.x + 2, WALL + y, WALL), (board.w - 4, 2, BOARD_RAIL))
    return m


def lid(b: Box) -> Manifold:
    """In print orientation: plate on the bed, lip up, cutouts mirrored in y."""
    L, W, _ = b.inner
    Lo, Wo, _ = b.outer
    lip_outer = _cube((WALL + FIT, WALL + FIT, WALL), (L - 2 * FIT, W - 2 * FIT, LID_LIP))
    lip_inner = _cube((WALL + FIT + 1.2, WALL + FIT + 1.2, WALL - 1),
                      (L - 2 * FIT - 2.4, W - 2 * FIT - 2.4, LID_LIP + 2))
    m = _cube((0, 0, 0), (Lo, Wo, WALL)) + (lip_outer - lip_inner)
    for c in b.cuts:
        if c.target != "lid":
            continue
        x, y, _ = c.at
        if c.kind == "box":
            sx, sy, _ = c.size
            m = m - _cube((x, Wo - y - sy, -1), (sx, sy, WALL + 2))
        else:
            m = m - _cyl(c.d, WALL + 2).translate([x, Wo - y, -1])
    return m


def lid_in_place(b: Box, m: Manifold) -> Manifold:
    """Flip the printed lid over onto the box (a rotation, so no mirror image)."""
    _, Wo, Ho = b.outer
    return m.rotate([180, 0, 0]).translate([0, Wo, Ho + WALL])


def _part_meshes(d: Design, b: Box) -> dict[str, tuple[trimesh.Trimesh, str]]:
    """ref -> (mesh, colour key) for everything inside the box, plus what lives outside."""
    out = {}
    for p in b.placed:
        x, y, z = WALL + p.x, WALL + p.y, b.z_of(p)
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
        else:
            body = _cube((x + p.w * 0.15, y + p.d * 0.15, z + 1.6), (p.w * 0.7, p.d * 0.7, body_h))
        out[p.ref] = (_mesh(pcb + body), p.category if p.category in COLORS else "module")

    # Probes, pumps and the like sit outside, lined up beside the cable wall.
    Lo, Wo, _ = b.outer
    x0 = Lo + 20
    for n, i in enumerate(i for i in d.instances if i.part.id in OUTSIDE and i.part.id != "psu-5v2a"):
        w, dd, h = i.part.size_mm
        out[i.ref] = (_mesh(_cube((x0, 5 + n * (min(w, dd) + 10), 0), (max(w, dd), min(w, dd), h))),
                      i.part.category if i.part.category in COLORS else "module")
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


def build(d: Design) -> dict:
    """All mesh outputs as bytes, plus the numbers the viewer and manifest need."""
    b = box(d)
    sh, ld = shell(b), lid(b)
    shell_mesh, lid_mesh = _mesh(sh), _mesh(ld)

    plate = trimesh.Scene()
    plate.add_geometry(shell_mesh, node_name="shell", geom_name="shell")
    plate.add_geometry(lid_mesh, node_name="lid", geom_name="lid",
                       transform=trimesh.transformations.translation_matrix([b.outer[0] + 10, 0, 0]))

    scene = trimesh.Scene()
    scene.add_geometry(_colored(shell_mesh, "shell"), node_name="shell", geom_name="shell")
    scene.add_geometry(_colored(_mesh(lid_in_place(b, ld)), "lid"), node_name="lid", geom_name="lid")
    for ref, (mesh, key) in _part_meshes(d, b).items():
        scene.add_geometry(_colored(mesh, key), node_name=ref, geom_name=ref)

    return {
        "shell.stl": shell_mesh.export(file_type="stl"),
        "lid.stl": lid_mesh.export(file_type="stl"),
        "print_plate.3mf": plate.export(file_type="3mf"),
        "assembly.glb": scene.export(file_type="glb"),
        "box": b,
        "volume_cm3": round((sh.volume() + ld.volume()) / 1000, 1),
        "watertight": bool(shell_mesh.is_watertight and lid_mesh.is_watertight),
    }


def load_glb(data: bytes) -> trimesh.Scene:
    return trimesh.load(io.BytesIO(data), file_type="glb")
