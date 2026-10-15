# Changelog

## 0.4.0

Complete devices: every build now comes with its cover, for three kinds of
machine, and every port and connector it needs.

- **Covers for three machines.** Each `.vbuild` carries the same cover three ways:
  shell and lid for a filament printer (3MF/STL), the same with a tighter lid
  fit for a resin printer (STL), and six finger-jointed panels for a laser
  cutter (SVG and DXF, 3 mm sheet, kerf allowed for, the top lifts off). All
  three hold the parts in the same places. Each says whether it fits a typical
  machine's build volume or bed.
- **Ports and connectors.** Probes that live outside plug into a GX12 panel
  socket, load cables come in through a PG7 gland, battery builds get a panel
  rocker switch in the + line, and the charger's USB-C socket now sits at the
  wall with its own opening. They are cut into the cover, listed in the BOM
  and the README, and wired in the 3D model, including each outside part's
  own lead to its port.
- **The viewer** has a "Cover made on" picker. It shows the filament print
  layer by layer, the resin print growing from its build plate and turning
  over, or the panels being laser-cut flat and folding into a box; then the
  ports slide into the wall. The download buttons follow the chosen cover.
- **Desktop app:** for a resin or laser cover, the machine button saves the
  files and opens them in the program your computer uses for them.
- **Format version 2.** `covers`, port nodes and wire ids are new; version 1
  files still open. See `docs/vbuild-format.md`.
- A fourth example, *Pond Probe*: a battery thermometer with a plug-in probe
  and an on/off switch.

Fixes
- The charger's USB socket could end up in the middle of the box while its
  opening was cut in the end wall. It is now placed against that wall.
- Wall slots narrower than the wall (vents in a 3 mm panel) were cut too wide.

## 0.3.0

- The project is named V-Hbuild everywhere: the app, the installer
  (`V-HbuildSetup`), the website, the docs, the Python package (`vhbuild`,
  so `python -m vhbuild`) and the settings (`VHBUILD_*` environment variables).
  Desktop settings now live in a `V-Hbuild` folder; set the model and printer
  again after upgrading.
- The website no longer shows Netlify's badge: its security policy only
  allows scripts from the site's own `/static/` folder.

- A static website (`site/`, `netlify.toml`): landing page, three example
  builds, and the 3D viewer running in the browser. The viewer opens
  same-site links (`viewer.html?src=...`) and offers the printer and firmware
  files straight from the loaded `.vbuild`, so it needs no server.

## 0.2.0

Fixes
- **ESP32-C3 serial output.** The DevKitM-1's USB port is a CP2102N bridge, but
  the generated firmware sent `Serial` to the chip's native USB, so nothing
  appeared in the serial monitor. Serial now goes to the bridge.
- **esptool 4.x.** V-Hbuild uses esptool 5's command names; the requirement now
  says `esptool>=5.0` instead of failing at the firmware step.
- **Empty plans.** A model that returned no parts produced a "passing" design
  with nothing but a board. The engine now rejects it and asks for a replan.
- **Clearer model errors.** A model that isn't pulled says `ollama pull <name>`;
  Ollama not running and timeouts say so, instead of a generic "not reachable"
  after a pointless CPU retry.
- **Live values in messages** now work for readings with dots in their names
  (the accelerometer's `s1_a.acceleration.x`).
- **Model notes are labelled.** The planner model's own notes now appear under
  "Notes from the model (unchecked)", apart from V-Hbuild's checked notes.
- **Network printers without a slicer** now explain what to set up.
- **The command line** prints a plain error message, not a traceback.

Platform
- Uploads are size-checked while streaming (32 MB default) instead of after
  reading them into memory, stored builds have a memory budget, and new builds
  get "busy" (429) when the queue is full. Tune with `VHBUILD_MAX_UPLOAD_MB`,
  `VHBUILD_MAX_HELD_MB` and `VHBUILD_MAX_QUEUED`.

Faster
- Firmware builds reuse a cached PlatformIO project per board and library set:
  about 8 s instead of about 30 s after the first build. `VHBUILD_CACHE_DIR`
  sets where the cache lives.

Also
- Low-power firmware explains that each rule runs once per wake.
- The version is in `/api/status` and in every `.vbuild` manifest.

## 0.1.0

First release.
