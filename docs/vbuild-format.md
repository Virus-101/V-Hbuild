# The `.vbuild` format, version 1

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
| `enclosure/enclosure.scad` | OpenSCAD | OpenSCAD, for editing the box |
| `firmware/firmware.bin` | ESP32 merged flash image | esptool, Web Serial flashers, the desktop app (written at `0x0`) |
| `firmware/firmware.uf2` | UF2 (RP2040 builds) | the board itself: copy onto its RPI-RP2 drive |
| `firmware/platformio.ini`, `firmware/src/main.cpp` | PlatformIO project | PlatformIO, to rebuild or change the firmware |
| `bom.csv`, `wiring.md`, `README.md`, `design.json` | CSV, Markdown, JSON | people |

The firmware image is present only when it was compiled at build time.
`machines.board.file` is `null` otherwise.

## Coordinates

Millimetres, **z up**. The origin is the outer bottom corner of the enclosure.
The GLB uses the same frame. A glTF viewer that assumes y-up will show the
model lying on its back, so set the camera's up vector to +z.

## `manifest.json`

```jsonc
{
  "format": "vbuild",            // always this
  "version": 1,                  // readers refuse versions newer than they know
  "name": "Desk Light Glows",
  "summary": "…", "idea": "the person's own words",
  "created": "2026-09-23T14:31:00Z",
  "generator": "V-Hbuild 0.3.0", "planner": "local model (llama3.2:3b)",
  "units": "mm", "up": "z",
  "ok": true,                    // passed every electrical check
  "issues": [{"severity": "info|warning|error", "message": "…", "replan": false}],
  "board": {"id": "esp32-c3-devkitm-1", "name": "…", "chip": "esp32c3"},
  "box": {"inner": [L, W, H], "outer": [L, W, H]},
  "model": "model/assembly.glb",

  // One entry per GLB node the viewer animates, keyed by node name
  // ("shell" and "lid" are always present in the GLB).
  "nodes": {
    "U1": {"label": "…", "category": "board", "center": [x, y, z], "size": [w, d, h],
           "on_lid": false, "outside": false},
    "S1": {"label": "…", "category": "sensor", "outside": true, "in_model": true}
  },

  // Wires as 3D polylines from a part to the board pin they land on.
  // Pin positions on the board are schematic; wiring.md is authoritative.
  "wires": [{"ref": "S1", "from": "S1 SDA", "to": "GPIO8", "note": "", "color": "#2a7de1",
             "points": [[x, y, z], [x, y, z], [x, y, z], [x, y, z]]}],

  // The build, in order. A step reveals nodes ("show") and wires (by part ref).
  // "action" asks the viewer for a special animation.
  "steps": [
    {"title": "Print the enclosure", "text": "…", "show": ["shell"], "action": "print"},
    {"title": "S1: …", "text": "…", "show": ["S1"], "wires": ["S1"]},
    {"title": "Flash the firmware", "text": "…", "show": [], "action": "flash"},
    {"title": "Close the lid", "text": "…", "show": ["lid"], "action": "close"}
  ],

  // What each machine takes.
  "machines": {
    "printer": {"files": ["enclosure/print_plate.3mf", "…"], "material": "PLA or PETG", "volume_cm3": 63.0},
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
