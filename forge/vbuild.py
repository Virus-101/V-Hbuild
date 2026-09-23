"""The .vbuild file: one idea, built.

A .vbuild is a zip. `manifest.json` at its root says what is inside and how
to show it; every other file is a standard format some machine or program
already reads:

    manifest.json               this format (docs/vbuild-format.md)
    model/assembly.glb          the finished device, one named node per part
    enclosure/print_plate.3mf   shell + lid, for any slicer / printer
    enclosure/shell.stl, lid.stl
    enclosure/enclosure.scad    editable source
    firmware/firmware.bin       merged ESP32 image, flash at 0x0   (or firmware.uf2 for RP2040)
    firmware/platformio.ini, firmware/src/main.cpp
    bom.csv, wiring.md, README.md, design.json

The viewer plays `steps` over the GLB: each step reveals parts (by node name)
and wires (3D polylines between a part and a board pin), so the person sees
their device being assembled. A machine only needs `machines`: which file it
takes and how.
"""
import hashlib
import io
import json
import time
import zipfile

from .enclosure import WALL, Box
from .engine import Design

FORMAT = "vbuild"
VERSION = 1
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024


class VbuildError(ValueError):
    pass


# --- where wires land -----------------------------------------------------

def _pin_anchors(d: Design, b: Box) -> dict[str, list[float]]:
    """Approximate header positions on the board, for drawing wires.

    Real pin order differs per board; the drawing shows which pin a wire goes
    to by label and wiring.md is the authority. Positions only need to be
    distinct and on the board's two header rows.
    """
    u1 = b.placed[0]
    x0, y0, z = WALL + u1.x, WALL + u1.y, b.z_of(u1) + 1.6
    rows = (y0 + 1.5, y0 + u1.d - 1.5)
    pins = ["3V3", "GND", "5V"] + [f"GPIO{g}" for g in sorted(set(d.board.digital) | set(d.board.analog)
                                                                | set(d.board.i2c))]
    pitch = max(1.2, min(2.54, (u1.w - 12) / max(1, (len(pins) + 1) // 2)))
    return {name: [round(x0 + 8 + (k // 2) * pitch, 2), round(rows[k % 2], 2), round(z, 2)]
            for k, name in enumerate(pins)}


def _board_end(target: str, anchors: dict) -> list[float] | None:
    t = target.split()[0] if target.startswith("GPIO") else target
    if t in anchors:
        return anchors[t]
    if "5V" in target or "VSYS" in target:
        return anchors["5V"]
    return None


def _nodes(d: Design, b: Box, glb_nodes: set[str]) -> dict:
    nodes = {"U1": {"label": d.board.name, "category": "board"}}
    for p in b.placed:
        z = b.z_of(p)
        nodes.setdefault(p.ref, {"label": p.label, "category": p.category})
        nodes[p.ref].update({
            "center": [round(WALL + p.x + p.w / 2, 2), round(WALL + p.y + p.d / 2, 2), round(z + p.h / 2, 2)],
            "size": [p.w, p.d, p.h], "on_lid": p.on_lid, "outside": False})
    for i in d.instances:
        if i.ref not in nodes:
            nodes[i.ref] = {"label": i.part.name, "category": i.part.category,
                            "outside": True, "in_model": i.ref in glb_nodes}
    return nodes


def _wires(d: Design, b: Box, nodes: dict) -> list[dict]:
    anchors = _pin_anchors(d, b)
    out = []
    for frm, to, note in d.wires:
        ref = frm.split()[0]
        node = nodes.get(ref)
        end = _board_end(to, anchors)
        if not node or end is None or "center" not in node:
            continue
        cx, cy, cz = node["center"]
        h = node["size"][2]
        start = [cx, cy, round(cz - h / 2 + 1.6, 2) if node.get("on_lid") else round(cz + h / 2, 2)]
        lift = max(start[2], end[2]) + 8
        signal = frm.split(maxsplit=1)[1] if " " in frm else frm
        color = {"VCC": "#d33", "GND": "#222"}.get(signal, "#e8a317" if "5V" in to else "#2a7de1")
        if signal == "VCC" and "3V3" in to:
            color = "#f06"
        out.append({"ref": ref, "from": frm, "to": to, "note": note, "color": color,
                    "points": [start, [start[0], start[1], lift], [end[0], end[1], lift], end]})
    return out


def _steps(d: Design, nodes: dict, wires: list[dict], firmware_file: str | None) -> list[dict]:
    steps = [{"title": "Print the enclosure",
              "text": "Print shell and lid from enclosure/print_plate.3mf (PLA, 0.2 mm layers, no supports).",
              "show": ["shell"], "action": "print"},
             {"title": f"Seat the {d.board.name}",
              "text": "Rest it on the two rails with the USB port in the wall cut-out.",
              "show": ["U1"]}]
    by_ref = {}
    for w in wires:
        by_ref.setdefault(w["ref"], []).append(w)
    order = [i for i in d.instances if i.rail and not nodes.get(i.ref, {}).get("on_lid")] + \
            [i for i in d.instances if i.rail and nodes.get(i.ref, {}).get("on_lid")] + \
            [i for i in d.instances if not i.rail]
    for inst in order:
        n = nodes.get(inst.ref, {})
        where = ("outside, through a cable gland" if n.get("outside")
                 else "under the lid, facing its opening" if n.get("on_lid") else "on the floor")
        conn = "; ".join(f"{w['from'].split(maxsplit=1)[-1]} to {w['to']}" for w in by_ref.get(inst.ref, []))
        steps.append({"title": f"{inst.ref}: {inst.part.name}",
                      "text": f"{inst.role[:1].upper()}{inst.role[1:]}. Mount it {where}." + (f" Wire {conn}." if conn else ""),
                      "show": [inst.ref], "wires": [inst.ref]})
    steps.append({"title": "Flash the firmware",
                  "text": (f"Connect USB and send firmware/{firmware_file} to the board."
                           if firmware_file else "Build firmware/ with PlatformIO and upload it."),
                  "show": [], "action": "flash"})
    steps.append({"title": "Close the lid", "text": "Press the lid on; the lip holds it.",
                  "show": ["lid"], "action": "close"})
    return steps


# --- writing and reading ---------------------------------------------------

def manifest(result: dict, d: Design, b: Box, glb_nodes: set[str], files: dict[str, bytes],
             firmware_file: str | None) -> dict:
    nodes = _nodes(d, b, glb_nodes)
    wires = _wires(d, b, nodes)
    board = d.board
    return {
        "format": FORMAT, "version": VERSION,
        "name": d.name, "summary": d.summary, "idea": result["idea"],
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": "Forge", "planner": result["planner"],
        "units": "mm", "up": "z",
        "ok": d.ok, "issues": [i.as_dict() for i in d.issues],
        "board": {"id": board.id, "name": board.name, "chip": board.chip},
        "box": {"inner": list(b.inner), "outer": list(b.outer)},
        "model": "model/assembly.glb",
        "nodes": nodes, "wires": wires,
        "steps": _steps(d, nodes, wires, firmware_file),
        "machines": {
            "printer": {"files": ["enclosure/print_plate.3mf", "enclosure/shell.stl", "enclosure/lid.stl"],
                        "material": "PLA or PETG", "volume_cm3": result.get("volume_cm3")},
            "board": {"chip": board.chip,
                      "file": f"firmware/{firmware_file}" if firmware_file else None,
                      "offset": "0x0" if firmware_file == "firmware.bin" else None,
                      "method": ("uf2-drive" if board.chip == "rp2040" else "esptool")
                      if firmware_file else None,
                      "source": "firmware/platformio.ini"},
        },
        "files": {p: hashlib.sha256(data).hexdigest() for p, data in sorted(files.items())},
    }


def write(man: dict, files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(man, indent=1))
        for path, data in sorted(files.items()):
            z.writestr(path, data)
    return buf.getvalue()


def read(data: bytes) -> tuple[dict, dict[str, bytes]]:
    """Open a .vbuild, refusing anything malformed, oversized or path-escaping."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise VbuildError("Not a .vbuild file (it is not a zip).")
    total, files = 0, {}
    for info in z.infolist():
        name = info.filename
        if info.is_dir():
            continue
        if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/") or ":" in name:
            raise VbuildError(f"Unsafe path in file: {name}")
        if info.file_size > MAX_FILE or (total := total + info.file_size) > MAX_TOTAL:
            raise VbuildError("The file is too large.")
        files[name] = z.read(info)
    if "manifest.json" not in files:
        raise VbuildError("No manifest.json - not a .vbuild file.")
    try:
        man = json.loads(files.pop("manifest.json"))
    except ValueError:
        raise VbuildError("manifest.json is not valid JSON.")
    if man.get("format") != FORMAT:
        raise VbuildError("manifest.json is not a vbuild manifest.")
    if not isinstance(man.get("version"), int) or man["version"] > VERSION:
        raise VbuildError(f"This file needs a newer Forge (format version {man.get('version')}).")
    for path, digest in (man.get("files") or {}).items():
        if path not in files:
            raise VbuildError(f"{path} is listed but missing.")
        if hashlib.sha256(files[path]).hexdigest() != digest:
            raise VbuildError(f"{path} is damaged (checksum mismatch).")
    return man, files
