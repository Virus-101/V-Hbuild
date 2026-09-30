# Changelog

## Unreleased

- A static website (`site/`, `netlify.toml`): landing page, three example
  builds, and the 3D viewer running in the browser. The viewer opens
  same-site links (`viewer.html?src=...`) and offers the printer and firmware
  files straight from the loaded `.vbuild`, so it needs no server.

## 0.2.0

Fixes
- **ESP32-C3 serial output.** The DevKitM-1's USB port is a CP2102N bridge, but
  the generated firmware sent `Serial` to the chip's native USB, so nothing
  appeared in the serial monitor. Serial now goes to the bridge.
- **esptool 4.x.** Forge uses esptool 5's command names; the requirement now
  says `esptool>=5.0` instead of failing at the firmware step.
- **Empty plans.** A model that returned no parts produced a "passing" design
  with nothing but a board. The engine now rejects it and asks for a replan.
- **Clearer model errors.** A model that isn't pulled says `ollama pull <name>`;
  Ollama not running and timeouts say so, instead of a generic "not reachable"
  after a pointless CPU retry.
- **Live values in messages** now work for readings with dots in their names
  (the accelerometer's `s1_a.acceleration.x`).
- **Model notes are labelled.** The planner model's own notes now appear under
  "Notes from the model (unchecked)", apart from Forge's checked notes.
- **Network printers without a slicer** now explain what to set up.
- **The command line** prints a plain error message, not a traceback.

Platform
- Uploads are size-checked while streaming (32 MB default) instead of after
  reading them into memory, stored builds have a memory budget, and new builds
  get "busy" (429) when the queue is full. Tune with `FORGE_MAX_UPLOAD_MB`,
  `FORGE_MAX_HELD_MB` and `FORGE_MAX_QUEUED`.

Faster
- Firmware builds reuse a cached PlatformIO project per board and library set:
  about 8 s instead of about 30 s after the first build. `FORGE_CACHE_DIR`
  sets where the cache lives.

Also
- Low-power firmware explains that each rule runs once per wake.
- The version is in `/api/status` and in every `.vbuild` manifest.

## 0.1.0

First release.
