"""The .vbuild file: one idea, built.

A .vbuild is a zip. `manifest.json` at its root says what is inside and how
to show it; every other file is a standard format some machine or program
already reads:

    manifest.json               this format (docs/vbuild-format.md)
    model/assembly.glb          the finished device, one named node per part
    enclosure/print_plate.3mf   shell + lid, for any slicer / filament printer
    enclosure/shell.stl, lid.stl
    enclosure/resin/shell.stl, lid.stl     the same cover for a resin printer
    enclosure/laser/panels.svg, .dxf       six finger-jointed panels for a laser cutter
    enclosure/enclosure.scad    editable source
    firmware/firmware.bin       merged ESP32 image, flash at 0x0   (or firmware.uf2 for RP2040)
    firmware/platformio.ini, firmware/src/main.cpp
    bom.csv, wiring.md, README.md, design.json

The viewer plays `steps` over the GLB: each step reveals parts (by node name)
and wires (3D polylines between a part, a port and a board pin), so the person
sees their device being assembled. `covers` swaps the first and last step for
the machine making the cover. A machine only needs `machines`: which file it
takes and how.
"""
import hashlib
import io
import json
import time
import zipfile

from . import __version__
from .enclosure import KERF, PROCESSES, Box
from .engine import Design

FORMAT = "vbuild"
VERSION = 2          # 2: covers for three machines, ports, wire ids
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
    x0, y0, z = b.wall + u1.x, b.wall + u1.y, b.z_of(u1) + 1.6
    rows = (y0 + 1.5, y0 + u1.d - 1.5)
    pins = ["3V3", "GND", "5V"] + [f"GPIO{g}" for g in sorted(set(d.board.digital) | set(d.board.analog)
                                                                | set(d.board.i2c))]
    pitch = max(1.2, min(2.54, (u1.w - 12) / max(1, (len(pins) + 1) // 2)))
    return {name: [round(x0 + 8 + (k // 2) * pitch, 2), round(rows[k % 2], 2), round(z, 2)]
            for k, name in enumerate(pins)}


def _r(p) -> list[float]:
    return [round(v, 2) for v in p]


def _nodes(d: Design, b: Box, glb_nodes: set[str]) -> dict:
    w = b.wall
    nodes = {"U1": {"label": d.board.name, "category": "board"}}
    for p in b.placed:
        z = b.z_of(p)
        nodes.setdefault(p.ref, {"label": p.label, "category": p.category})
        nodes[p.ref].update({
            "center": _r([w + p.x + p.w / 2, w + p.y + p.d / 2, z + p.h / 2]),
            "size": [p.w, p.d, p.h], "on_lid": p.on_lid, "outside": False})
    for pt in b.ports:
        X, y, z = b.port_at(pt)
        c = pt.connector
        nodes[pt.ref] = {
            "label": c.name or next(i.part.name for i in d.instances if i.ref == pt.ref),
            "category": "power" if pt.kind == "rocker" else "port", "port": pt.kind,
            "serves": pt.serves, "what": pt.label, "outside": False, "in_model": pt.ref in glb_nodes,
            "center": _r([X, y, z]), "inner": _r([X - w - 15, y, z]), "outer": _r([X + c.reach, y, z])}
    for p in b.outside:
        port = next((pt.ref for pt in b.ports if pt.serves == p.ref), None)
        nodes[p.ref] = {"label": p.label, "category": p.category, "outside": True,
                        "in_model": p.ref in glb_nodes, "port": port,
                        "center": _r([w + p.x + p.w / 2, w + p.y + p.d / 2, p.h / 2]),
                        "size": [p.w, p.d, p.h]}
    for i in d.instances:
        if i.ref not in nodes:
            nodes[i.ref] = {"label": i.part.name, "category": i.part.category,
                            "outside": True, "in_model": i.ref in glb_nodes}
    return nodes


def _attach(nodes: dict, ref: str) -> list[float] | None:
    """Where a wire meets a part: its port's inside end when it has one."""
    n = nodes.get(ref)
    if not n or "center" not in n:
        return None
    if n.get("port") and n.get("outside"):
        return nodes[n["port"]]["inner"]
    if "inner" in n:
        return n["inner"]
    cx, cy, cz = n["center"]
    h = n["size"][2]
    return [cx, cy, round(cz - h / 2 + 1.6, 2) if n.get("on_lid") else round(cz + h / 2, 2)]


def _end(target: str, anchors: dict, nodes: dict) -> list[float] | None:
    t = target.split()[0] if target.startswith("GPIO") else target
    if t in anchors:
        return anchors[t]
    if "5V" in target or "VSYS" in target:
        return anchors["5V"]
    return _attach(nodes, target.split()[0])


def _wires(d: Design, b: Box, nodes: dict) -> list[dict]:
    anchors = _pin_anchors(d, b)
    out = []
    for frm, to, note in d.wires:
        ref = frm.split()[0]
        start, end = _attach(nodes, ref), _end(to, anchors, nodes)
        if start is None or end is None:
            continue
        lift = max(start[2], end[2]) + 8
        signal = frm.split(maxsplit=1)[1] if " " in frm else frm
        color = {"VCC": "#d33", "GND": "#222"}.get(signal, "#e8a317" if "5V" in to else "#2a7de1")
        if signal == "VCC" and "3V3" in to:
            color = "#f06"
        if "OUT+" in signal or signal in ("+", "IN", "OUT", "+/-", "COM"):
            color = "#d33"
        elif signal.endswith("-"):
            color = "#222"
        other = to.split()[0]
        out.append({"id": f"w{len(out) + 1}", "ref": ref,
                    "to_ref": other if other in nodes and other != ref else None,
                    "from": frm, "to": to, "note": note, "color": color,
                    "points": [start, [start[0], start[1], lift], [end[0], end[1], lift], end]})
    # Each outside part's own lead, from the part to its port.
    for ref, n in nodes.items():
        if not (n.get("outside") and n.get("port") and "center" in n):
            continue
        cx, cy, cz = n["center"]
        top = cz + n["size"][2] / 2
        ox, oy, oz = nodes[n["port"]]["outer"]
        up = max(top, oz) + 10
        out.append({"id": f"w{len(out) + 1}", "ref": ref, "to_ref": n["port"],
                    "from": f"{ref} lead", "to": n["port"], "note": "the part's own cable",
                    "color": "#3a3a3a", "cable": True,
                    "points": [[cx, cy, round(top, 2)], [cx, cy, round(up, 2)], [ox + 4, oy, round(up, 2)],
                               [ox + 4, oy, oz], [ox, oy, oz]]})
    return out


def cover_steps(proc: str, files: dict | None = None) -> tuple[dict, dict]:
    """The first and last step of a build, which depend on the machine making the cover."""
    p = PROCESSES[proc]
    if proc == "laser":
        names = [f"laser_{n}" for n in ("bottom", "front", "back", "left", "right", "top")]
        make = {"title": "Laser-cut the panels",
                "text": f"Cut enclosure/laser/panels.svg from {p.material}. Glue the bottom and the "
                        "four walls together; keep the top aside.",
                "show": names, "action": "laser"}
        close = {"title": "Fit the top", "text": "Press the top onto the finger joints. It lifts off "
                                                 "again for access.",
                 "show": ["laser_top"], "action": "laser_lid"}
    elif proc == "resin":
        make = {"title": "Resin-print the enclosure",
                "text": f"Print enclosure/resin/shell.stl and lid.stl in {p.material}. {p.how}",
                "show": ["resin_shell"], "action": "resin"}
        close = {"title": "Close the lid", "text": "Press the lid on; resin holds a tighter fit than "
                                                   "filament, so ease it down evenly.",
                 "show": ["resin_lid"], "action": "close"}
    else:
        make = {"title": "Print the enclosure",
                "text": f"Print shell and lid from enclosure/print_plate.3mf ({p.material}; {p.how})",
                "show": ["shell"], "action": "print"}
        close = {"title": "Close the lid", "text": "Press the lid on; the lip holds it.",
                 "show": ["lid"], "action": "close"}
    return {**make, "cover": "make"}, {**close, "cover": "close"}


def _steps(d: Design, b: Box, nodes: dict, wires: list[dict], firmware_file: str | None) -> list[dict]:
    make, close = cover_steps("fdm")
    steps = [make]
    if b.ports:
        listed = "; ".join(f"{pt.ref}, {pt.label}" for pt in b.ports)
        steps.append({"title": "Fit the ports and connectors",
                      "text": f"Push each one through its hole in the connector wall and tighten its nut "
                              f"from inside: {listed}.",
                      "show": [pt.ref for pt in b.ports]})
    usb = [p.ref for p in b.placed if p.panel == "usb" and p.ref != "U1"]
    steps.append({"title": f"Seat the {d.board.name}",
                  "text": "Rest it on the two rails (on 3 mm standoffs in a laser-cut box) with its USB "
                          "port in the wall opening.",
                  "show": ["U1"]})
    order = [i for i in d.instances if i.rail and not nodes.get(i.ref, {}).get("on_lid")] + \
            [i for i in d.instances if i.rail and nodes.get(i.ref, {}).get("on_lid")] + \
            [i for i in d.instances if not i.rail]
    index = {inst.ref: len(steps) + k for k, inst in enumerate(order)}
    by_step: dict[int, list[dict]] = {}
    for wire in wires:
        k = max(index.get(wire["ref"], 0), index.get(wire.get("to_ref") or "", 0))
        by_step.setdefault(k, []).append(wire)
    for inst in order:
        n = nodes.get(inst.ref, {})
        port = n.get("port")
        if n.get("outside") and port:
            where = (f"outside; its plug goes into {port}" if nodes[port]["port"] == "gx12"
                     else f"outside; its cable comes in through {port}")
        elif n.get("outside"):
            where = "outside the box"
        elif port:
            where = "in the connector wall"
        elif inst.ref in usb:
            where = "on the floor with its USB-C socket in the end-wall opening"
        else:
            where = "under the lid, facing its opening" if n.get("on_lid") else "on the floor"
        mine = by_step.get(index[inst.ref], [])
        conn = "; ".join(f"{w['from'].split(maxsplit=1)[-1]} to {w['to']}" for w in mine if not w.get("cable"))
        mount = (" Its cable plugs into the board's USB port through the end-wall opening."
                 if inst.part.id == "psu-5v2a" else f" Mount it {where}.")
        steps.append({"title": f"{inst.ref}: {inst.part.name}",
                      "text": f"{inst.role[:1].upper()}{inst.role[1:]}." + mount + (f" Wire {conn}." if conn else ""),
                      "show": [inst.ref], "wires": [w["id"] for w in mine]})
    steps.append({"title": "Flash the firmware",
                  "text": (f"Connect USB and send firmware/{firmware_file} to the board."
                           if firmware_file else "Build firmware/ with PlatformIO and upload it."),
                  "show": [], "action": "flash"})
    steps.append(close)
    return steps


def _covers(info: dict) -> dict:
    out = {}
    for proc, p in PROCESSES.items():
        make, close = cover_steps(proc)
        nodes = {"fdm": ["shell", "lid"], "resin": ["resin_shell", "resin_lid"]}.get(
            proc, [f"laser_{n}" for n in ("bottom", "front", "back", "left", "right", "top")])
        out[proc] = {"label": p.label, "machine": p.machine, "material": p.material, "how": p.how,
                     "nodes": nodes, "make": make, "close": close, **info.get(proc, {})}
    return out


# --- writing and reading ---------------------------------------------------

def manifest(result: dict, d: Design, b: Box, glb_nodes: set[str], files: dict[str, bytes],
             firmware_file: str | None) -> dict:
    nodes = _nodes(d, b, glb_nodes)
    wires = _wires(d, b, nodes)
    board = d.board
    covers = _covers(result.get("covers") or {})
    return {
        "format": FORMAT, "version": VERSION,
        "name": d.name, "summary": d.summary, "idea": result["idea"],
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": f"V-Hbuild {__version__}", "planner": result["planner"],
        "units": "mm", "up": "z",
        "ok": d.ok, "issues": [i.as_dict() for i in d.issues],
        "board": {"id": board.id, "name": board.name, "chip": board.chip},
        "box": {"inner": list(b.inner), "outer": list(b.outer)},
        "model": "model/assembly.glb",
        "nodes": nodes, "wires": wires,
        "steps": _steps(d, b, nodes, wires, firmware_file),
        "cover": "fdm", "covers": covers,
        "machines": {
            "printer": {"files": ["enclosure/print_plate.3mf", "enclosure/shell.stl", "enclosure/lid.stl"],
                        "material": PROCESSES["fdm"].material, "volume_cm3": result.get("volume_cm3")},
            "resin_printer": {"files": ["enclosure/resin/shell.stl", "enclosure/resin/lid.stl"],
                              "material": PROCESSES["resin"].material,
                              "volume_ml": covers["resin"].get("resin_ml")},
            "laser_cutter": {"files": ["enclosure/laser/panels.svg", "enclosure/laser/panels.dxf"],
                             "material": PROCESSES["laser"].material, "kerf_mm": KERF,
                             "sheet_mm": covers["laser"].get("sheet_mm")},
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
        raise VbuildError(f"This file needs a newer V-Hbuild (format version {man.get('version')}).")
    for path, digest in (man.get("files") or {}).items():
        if path not in files:
            raise VbuildError(f"{path} is listed but missing.")
        if hashlib.sha256(files[path]).hexdigest() != digest:
            raise VbuildError(f"{path} is damaged (checksum mismatch).")
    return man, files
