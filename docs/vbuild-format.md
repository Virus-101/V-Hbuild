# The `.vbuild` format, version 2

A `.vbuild` is one device idea, fully designed: everything needed to show it
being built and to build it for real. It is a **zip file**. `manifest.json`
at the root describes it. Every other file is in a standard format some
existing program or machine already reads.

| Path | Format | Read by |
| --- | --- | --- |
| `manifest.json` | JSON (below) | the V-Hbuild viewer, the desktop app |
| `model/assembly.glb` | glTF 2.0 binary | the viewer; any 3D tool (Blender, Windows 3D Viewer) |
| `enclosure/print_plate.3mf` | 3MF | every mainstream slicer (PrusaSlicer, Orca, Bambu Studio, Cura) |
| `enclosure/shell.stl`, `lid.stl` | binary STL | any slicer |
| `enclosure/resin/shell.stl`, `lid.stl` | binary STL | resin slicers (Chitubox, Lychee, PrusaSlicer SLA) |
| `enclosure/laser/panels.svg`, `panels.dxf` | SVG / DXF R12, mm, 1:1 | LightBurn, LaserGRBL, Inkscape, RDWorks |
| `enclosure/enclosure.scad` | OpenSCAD | OpenSCAD, for editing the box |
| `firmware/firmware.bin` | ESP32 merged flash image | esptool, Web Serial flashers, the desktop app (written at `0x0`) |
| `firmware/firmware.uf2` | UF2 (RP2040 builds) | the board itself: copy onto its RPI-RP2 drive |
| `firmware/platformio.ini`, `firmware/src/main.cpp` | PlatformIO project | PlatformIO, to rebuild or change the firmware |
| `bom.csv`, `wiring.md`, `README.md`, `design.json` | CSV, Markdown, JSON | people |

The firmware image is present only when it was compiled at build time.
`machines.board.file` is `null` otherwise.

## Coordinates

Millimetres, **z up**. The origin is the outer bottom corner of the printed
enclosure (2 mm walls). The laser-cut box has 3 mm walls; it is drawn shifted
so its inside matches, which puts its outer corner at (-1, -1, -1).
The GLB uses the same frame. A glTF viewer that assumes y-up will show the
model lying on its back, so set the camera's up vector to +z.

## `manifest.json`

```jsonc
{
  "format": "vbuild",            // always this
  "version": 2,                  // readers refuse versions newer than they know
  "name": "Desk Light Glows",
  "summary": "…", "idea": "the person's own words",
  "created": "2026-09-23T14:31:00Z",
  "generator": "V-Hbuild 0.4.0", "planner": "local model (llama3.2:3b)",
  "units": "mm", "up": "z",
  "ok": true,                    // passed every electrical check
  "issues": [{"severity": "info|warning|error", "message": "…", "replan": false}],
  "board": {"id": "esp32-c3-devkitm-1", "name": "…", "chip": "esp32c3"},
  "box": {"inner": [L, W, H], "outer": [L, W, H]},
  "model": "model/assembly.glb",

  // One entry per GLB node the viewer animates, keyed by node name. The GLB
  // also holds every cover's nodes (see "covers").
  "nodes": {
    "U1": {"label": "…", "category": "board", "center": [x, y, z], "size": [w, d, h],
           "on_lid": false, "outside": false},
    // A port: a panel part through the connector wall. "inner" and "outer" are
    // where wires meet it inside and where the outside part's lead plugs in.
    "J1": {"label": "GX12 aviation connector, …", "category": "port", "port": "gx12 | gland | rocker",
           "serves": "S1", "center": [x, y, z], "inner": [x, y, z], "outer": [x, y, z]},
    "S1": {"label": "…", "category": "sensor", "outside": true, "in_model": true, "port": "J1",
           "center": [x, y, z], "size": [w, d, h]}
  },

  // Wires as 3D polylines between a part (or its port), another part and the
  // board pin they land on. "cable": true marks an outside part's own lead.
  // Pin positions on the board are schematic; wiring.md is authoritative.
  "wires": [{"id": "w1", "ref": "S1", "to_ref": null, "from": "S1 SDA", "to": "GPIO8", "note": "",
             "color": "#2a7de1", "points": [[x, y, z], [x, y, z], [x, y, z], [x, y, z]]}],

  // The build, in order. A step reveals nodes ("show") and wires (by "id";
  // version 1 files listed part refs). "action" asks the viewer for a special
  // animation. Steps marked "cover" are replaced by the chosen cover's own.
  "steps": [
    {"title": "Print the enclosure", "text": "…", "show": ["shell"], "action": "print", "cover": "make"},
    {"title": "Fit the ports and connectors", "text": "…", "show": ["J1"]},
    {"title": "S1: …", "text": "…", "show": ["S1"], "wires": ["w1", "w2"]},
    {"title": "Flash the firmware", "text": "…", "show": [], "action": "flash"},
    {"title": "Close the lid", "text": "…", "show": ["lid"], "action": "close", "cover": "close"}
  ],

  // The cover, for three kinds of machine. "cover" names the default.
  "cover": "fdm",
  "covers": {
    "fdm":   {"label": "3D printer (filament)", "machine": "printer", "nodes": ["shell", "lid"],
              "make": {…step, "action": "print"}, "close": {…step, "action": "close"},
              "outer": [L, W, H], "fits": true, "fit_note": "…", "volume_cm3": 54.8},
    "resin": {"machine": "resin_printer", "nodes": ["resin_shell", "resin_lid"],
              "make": {…, "action": "resin"}, "close": {…}, "resin_ml": 54.9, "…": "…"},
    "laser": {"machine": "laser_cutter", "nodes": ["laser_bottom", "…", "laser_top"],
              "make": {…, "action": "laser"}, "close": {…, "action": "laser_lid"},
              "sheet_mm": [240, 161], "sheet_note": "…", "flat_box": [[x0, y0], [x1, y1]],
              // How each panel lies on the cutting bed: turn it by "quat" (x, y, z, w)
              // about "center", then move "center" to "flat_center".
              "poses": {"laser_front": {"center": [x, y, z], "flat_center": [x, y, z], "quat": [x, y, z, w]}}}
  },

  // What each machine takes.
  "machines": {
    "printer": {"files": ["enclosure/print_plate.3mf", "…"], "material": "PLA or PETG", "volume_cm3": 63.0},
    "resin_printer": {"files": ["enclosure/resin/shell.stl", "enclosure/resin/lid.stl"], "volume_ml": 54.9},
    "laser_cutter": {"files": ["enclosure/laser/panels.svg", "enclosure/laser/panels.dxf"],
                     "material": "3 mm plywood or acrylic", "kerf_mm": 0.15, "sheet_mm": [240, 161]},
    "board": {"chip": "esp32c3", "file": "firmware/firmware.bin", "offset": "0x0",
              "method": "esptool | uf2-drive | null", "source": "firmware/platformio.ini"}
  },

  // SHA-256 of every other file. Readers reject a file whose content does not match.
  "files": {"model/assembly.glb": "9f2c…", "…": "…"}
}
```

## Reading one safely

`vhbuild.vbuild.read()` is the reference reader. It rejects:

- archives without a `manifest.json`, or a manifest whose `format` is not `vbuild`
- a `version` newer than it understands
- any entry whose path is absolute, contains `..`, or has a drive letter (zip-slip)
- any file over 64 MB, or more than 256 MB in total
- any file listed in `files` that is missing or fails its checksum

## Flashing by hand

- **ESP32** (`method: esptool`): `esptool --chip <chip> write-flash 0x0 firmware/firmware.bin`
- **RP2040** (`method: uf2-drive`): hold BOOTSEL, plug the board in, and copy
  `firmware/firmware.uf2` onto the RPI-RP2 drive.
