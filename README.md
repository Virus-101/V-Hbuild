# Forge: describe a device, watch it get built

You describe a device in plain words, for example *"a plant monitor that waters
my basil when the soil is dry"*. Forge designs it with a **local AI model** and hands you
a **`.vbuild` file**. Open that file and you watch the device being built: the
enclosure prints layer by layer, each part drops into place, the wires draw
themselves to their pins, the board is flashed, and the lid closes.

The same file drives the real machines:

- **The 3D printer** gets the enclosure: a 3MF any slicer opens, or G-code sent
  straight to OctoPrint or Klipper.
- **The board** gets the firmware: a compiled image flashed over USB, or a UF2
  file for the Pico W.

```
idea ─► the model plans (parts only) ─► engine checks the electronics ─► replan if rejected
                                            │
      firmware (compiled) ◄─────────────────┤
      enclosure (STL / 3MF, exact booleans) ◄┤
      3D assembly (GLB) + build steps ◄──────┘
                                            ▼
                                     device.vbuild
                        ┌───────────────────┼────────────────────┐
                  3D viewer            3D printer          microcontroller
          (web or desktop app)  (3MF / OctoPrint / Klipper)  (USB flash / UF2)
```

## Three ways to use it

**The platform** (a web server, for everyone):

```powershell
pip install -r requirements.txt
python -m uvicorn forge.web:app --host 0.0.0.0 --port 8780
```

or `docker compose up -d`, which also runs the local model in Ollama (see `models/README.md`).
People build in the browser, watch their device in the 3D viewer, and download
the `.vbuild` file or the printer and firmware files individually.

**The desktop app** (connects to the machines):

```powershell
pip install -r requirements-desktop.txt
python -m forge.desktop                  # or: python -m forge.desktop device.vbuild
```

The desktop app does everything the platform does, and adds the machines:

- **Print enclosure** slices the 3MF and sends it to OctoPrint, Klipper, or a folder.
- **Open in my slicer** hands the 3MF to the slicer you already use.
- **Flash board** writes the firmware over USB.

Printer and model settings live under **Settings**. The Windows installer
(`ForgeSetup-x.y.z.exe`) is built by GitHub Actions (`.github/workflows/desktop.yml`,
on a `v*` tag or run by hand). It registers `.vbuild`, so double-clicking one
opens it in Forge.

**The command line:**

```powershell
python -m forge "a desk light that turns red when someone walks in" -o out/
```

## The model: local first

Forge plans with a model served by [Ollama](https://ollama.com) on your own
machine. The default is `llama3.2:3b` (`ollama pull llama3.2:3b`). Any Ollama
model works, including your own GGUF; see `models/README.md`.

A small model on its own would invent parts and wiring. Forge doesn't let it:

- **The output is held to a schema.** Ollama turns the plan's JSON schema into a
  grammar, and the part and board fields are enums of the catalog. The model
  cannot write a part that doesn't exist.
- **The model never does the electronics.** It picks parts; `forge/engine.py`
  assigns every pin and I2C address, sizes the power rails, and adds the
  resistors, capacitors, relay, charger and boost converter. Plans the engine
  rejects go back to the model with the reasons.
- **Plans are held to the idea.** A review step removes outputs the idea
  never asked for (a servo, a buzzer, a button) and duplicate sensors, and
  records each change.
- **The model writes rules, not C++.** For the behaviour it picks rules from a
  menu of this device's variables and actions, e.g. "every 10 minutes, if soil
  moisture is below 35%, run the pump for 3 seconds". Forge generates the C++
  from those rules and compile-checks it before it ships.

Claude can be selected as a second planner (`ANTHROPIC_API_KEY`). The offline
keyword planner needs no model at all. Set `FORGE_PROVIDER` to
`auto | local | claude | offline`. `auto` picks the local model whenever Ollama
is serving it.

To make a local model better at this over time, set
`FORGE_TRAINING_LOG=train.jsonl`. Every plan the engine accepts is saved as an
idea → plan example to fine-tune on.

## The `.vbuild` file

A zip holding a manifest and standard files: GLB, 3MF, STL, the firmware
image, the PlatformIO source, the BOM and the wiring table. The full spec is in
[docs/vbuild-format.md](docs/vbuild-format.md).

## What you need

| For | You need |
| --- | --- |
| Planning with a local model | [Ollama](https://ollama.com) with a model pulled (default `llama3.2:3b`) |
| Compiled firmware in the file | [PlatformIO](https://platformio.org) (`pip install platformio`) on the machine that builds. The Docker image includes it. Without it, the file ships the source |
| Printing from the app | A slicer with a command line (PrusaSlicer, Orca), plus OctoPrint or Moonraker, or just a folder. Without a slicer, "Open in my slicer" still works |
| Flashing from the app | A USB cable, and the board's USB driver (CP210x or CH340 on many ESP32 boards). For the Pico W, hold BOOTSEL while plugging in |
| Building the device | The parts in `bom.csv` (about $20–50) and a 3D printer or print service |

## Project layout

| Path | What it does |
| --- | --- |
| `forge/catalog.py` | the parts Forge may use, with datasheet facts |
| `forge/llm.py` | the local model (Ollama) and Claude, behind one "fill this schema" call |
| `forge/planner.py` | idea → plan, plus the offline keyword planner |
| `forge/engine.py` | pins, addresses, power, passives, rule checks |
| `forge/firmware.py`, `forge/compile.py` | firmware source → flashable image |
| `forge/enclosure.py`, `forge/geometry.py` | box layout and cutouts → OpenSCAD, STL, 3MF, GLB |
| `forge/vbuild.py` | the `.vbuild` writer and safe reader |
| `forge/machines.py` | USB flashing, UF2 drives, OctoPrint / Moonraker / folder, slicing |
| `forge/web.py` | the server: build jobs, files, desktop-only machine API |
| `forge/desktop.py` | the desktop app: local server + native window |
| `forge/static/` | the web UI and the 3D viewer (three.js, bundled for offline use) |

## Tests

```powershell
pip install -r requirements-dev.txt
pytest                                    # about 2 s
$env:FORGE_TEST_COMPILE=1; pytest -k real_compile   # compiles an ESP32 and a Pico W image
```

The suite covers:

- the engine rules
- the local model's requests (schema, retry, CPU fallback) against a stand-in Ollama
- geometry: watertight meshes, and lid holes that line up after the flip
- the `.vbuild` reader (checksums, zip-slip)
- OctoPrint and Moonraker uploads against a local stand-in printer
- UF2 and esptool flashing
- the web and desktop APIs, including the desktop-token check

## Limits, honestly

- **The catalog is small:** 3 boards and 16 modules. Anything else the idea asks
  for lands in "not covered".
- **Modules and wires, not a custom PCB.** Everything is built from ready-made
  modules and jumper wires. The wire list in `design.json` would be the starting
  point for a PCB.
- **The box is simple.** The enclosure is a packed rectangular box. Check the
  fit before a long print.
- **Wire drawings are schematic.** Pin positions on the 3D board are
  illustrative; `wiring.md` is the authority.
- **A small model makes weaker choices** than a large one. The engine keeps those
  choices electrically correct, but not always the best parts for the idea.
  Fine-tuning on the training log is how that improves.

## Website

`site/` is the project website: a landing page, three example builds and the
3D viewer, all static. `netlify.toml` builds it (`python3 scripts/build_site.py`
 into `site-dist/`), so connecting the repository to Netlify publishes it. The
examples are regenerated with `python scripts/make_samples.py`.

## Changes

See [CHANGELOG.md](CHANGELOG.md).

## License

MIT - see [LICENSE](LICENSE). The bundled three.js and fflate libraries in
`forge/static/vendor/` keep their own MIT licenses, included next to them.
