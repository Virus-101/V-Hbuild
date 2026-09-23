"""The machines a .vbuild is sent to, from the desktop app.

Board:   ESP32 over USB serial with esptool (the merged image, at 0x0), or an
         RP2040 by copying the .uf2 onto the RPI-RP2 drive it shows up as in
         BOOTSEL mode.
Printer: OctoPrint or Moonraker (Klipper) over their HTTP APIs, or a plain
         folder (an SD card, a slicer's watched folder). Printers take G-code,
         so the 3MF is sliced first with a slicer's command line when one is
         configured; otherwise the 3MF is opened in the person's own slicer.

Only the desktop app exposes these. A hosted platform never touches USB.
"""
import contextlib
import io
import json
import os
import platform
import shlex
import string
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# USB vendor ids of the serial bridges dev boards use.
KNOWN_VIDS = {0x303A: "Espressif (native USB)", 0x10C4: "Silicon Labs CP210x",
              0x1A86: "WCH CH340", 0x0403: "FTDI", 0x2E8A: "Raspberry Pi"}

DEFAULT_SETTINGS = {
    "provider": "auto",                  # auto | local | claude | offline
    "ollama_url": "http://localhost:11434",
    "local_model": "llama3.2:3b",
    "printer": {
        "kind": "none",                  # none | octoprint | moonraker | folder
        "url": "", "api_key": "", "folder": "",
        "start_print": False,
        # PrusaSlicer-style CLI; {in} {out} {profile} are filled in.
        "slicer": "", "profile": "",
        "slicer_args": "--export-gcode --load {profile} -o {out} {in}",
    },
}


class MachineError(RuntimeError):
    pass


# --- settings --------------------------------------------------------------

def settings_path() -> Path:
    if os.environ.get("FORGE_SETTINGS"):
        return Path(os.environ["FORGE_SETTINGS"])
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "Forge" / "settings.json"


def load_settings() -> dict:
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        saved = json.loads(settings_path().read_text())
    except (OSError, ValueError):
        return s
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            s[k].update({kk: vv for kk, vv in v.items() if kk in s[k]})
        elif k in s:
            s[k] = v
    return s


def save_settings(new: dict) -> dict:
    s = load_settings()
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            s[k].update({kk: vv for kk, vv in v.items() if kk in s[k]})
        elif k in s:
            s[k] = v
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(s, indent=2))
    return s


# --- boards ----------------------------------------------------------------

def serial_ports() -> list[dict]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    out = []
    for p in list_ports.comports():
        out.append({"device": p.device, "description": p.description or "",
                    "bridge": KNOWN_VIDS.get(p.vid or -1, ""), "likely_board": (p.vid or -1) in KNOWN_VIDS})
    return sorted(out, key=lambda p: (not p["likely_board"], p["device"]))


def _mount_roots() -> list[Path]:
    if sys.platform == "win32":
        return [Path(f"{d}:\\") for d in string.ascii_uppercase]
    if sys.platform == "darwin":
        return list(Path("/Volumes").glob("*"))
    roots = []
    for base in (Path("/media"), Path("/run/media"), Path("/mnt")):
        roots += list(base.glob("*")) + list(base.glob("*/*"))
    return roots


def uf2_drives(roots: list[Path] | None = None) -> list[dict]:
    """Drives a board in UF2 bootloader mode presents (they carry INFO_UF2.TXT)."""
    found = []
    for root in roots if roots is not None else _mount_roots():
        info = root / "INFO_UF2.TXT"
        try:
            if info.is_file():
                text = info.read_text(errors="replace")
                board = next((l.split(":", 1)[1].strip() for l in text.splitlines()
                              if l.lower().startswith("board-id")), "")
                found.append({"path": str(root), "board": board})
        except OSError:
            continue
    return found


def flash_uf2(drive: str, data: bytes) -> str:
    target = Path(drive) / "firmware.uf2"
    if not (Path(drive) / "INFO_UF2.TXT").exists():
        raise MachineError(f"{drive} is not a UF2 bootloader drive. Hold BOOTSEL while plugging the board in.")
    target.write_bytes(data)     # the board reboots into the new firmware on its own
    return f"Copied {len(data) // 1024} KB to {target}. The board restarts by itself."


def flash_esp32(port: str, chip: str, data: bytes, offset: str = "0x0", baud: int = 460800) -> str:
    """Write the merged image. Returns esptool's log; raises MachineError on failure."""
    import esptool
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(data)
        image = f.name
    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            esptool.main(["--chip", chip, "--port", port, "--baud", str(baud),
                          "write-flash", offset, image])
    except SystemExit as e:
        if e.code not in (0, None):
            raise MachineError(log.getvalue()[-2000:] or f"esptool exited with {e.code}")
    except Exception as e:     # esptool raises FatalError and serial errors
        raise MachineError(f"{e}\n{log.getvalue()[-2000:]}")
    finally:
        os.unlink(image)
    return log.getvalue()[-4000:]


def flash(manifest: dict, files: dict[str, bytes], target: str) -> str:
    """Flash whatever the manifest says the board takes. target = port or drive."""
    board = manifest.get("machines", {}).get("board", {})
    path = board.get("file")
    if not path or path not in files:
        raise MachineError("This .vbuild has no compiled firmware. Build it again with PlatformIO "
                           "installed, or build firmware/ yourself.")
    if board.get("method") == "uf2-drive":
        return flash_uf2(target, files[path])
    return flash_esp32(target, board.get("chip") or "auto", files[path], board.get("offset") or "0x0")


# --- printers --------------------------------------------------------------

def _multipart(fields: dict, file_field: str, filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                 f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode())
    parts += [data, f"\r\n--{boundary}--\r\n".encode()]
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _post(url: str, body: bytes, content_type: str, headers: dict) -> dict:
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": content_type, **headers})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise MachineError(f"The printer answered {e.code}: {e.read()[:300].decode('utf-8', 'replace')}")
    except (urllib.error.URLError, OSError) as e:
        raise MachineError(f"Could not reach the printer at {url}: {e}")
    try:
        return json.loads(text)
    except ValueError:
        return {"raw": text[:300]}


def slice_3mf(model: bytes, cfg: dict) -> bytes:
    slicer = cfg.get("slicer", "")
    if not slicer:
        raise MachineError("No slicer configured.")
    with tempfile.TemporaryDirectory(prefix="forge-slice-") as tmp:
        src, out = Path(tmp) / "plate.3mf", Path(tmp) / "plate.gcode"
        src.write_bytes(model)
        template = shlex.split(cfg.get("slicer_args") or DEFAULT_SETTINGS["printer"]["slicer_args"],
                               posix=os.name != "nt")
        if not cfg.get("profile"):     # no profile: drop "--load {profile}"
            template = [a for a in template if a not in ("--load", "{profile}")]
        args = [a.format(**{"in": str(src), "out": str(out), "profile": cfg.get("profile", "")})
                for a in template]
        try:
            run = subprocess.run([slicer, *args], capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise MachineError(f"The slicer did not run: {e}")
        if run.returncode != 0 or not out.exists():
            raise MachineError("Slicing failed:\n" + (run.stdout + run.stderr)[-1500:])
        return out.read_bytes()


def send_to_printer(name: str, model_3mf: bytes, cfg: dict) -> str:
    kind = cfg.get("kind", "none")
    if kind == "none":
        raise MachineError("No printer is set up. Add one in Settings, or open the 3MF in your slicer.")
    if kind == "folder":
        folder = Path(cfg.get("folder") or "")
        if not folder.is_dir():
            raise MachineError(f"Printer folder {folder} does not exist.")
        if cfg.get("slicer"):
            (folder / f"{name}.gcode").write_bytes(slice_3mf(model_3mf, cfg))
            return f"Saved {name}.gcode to {folder}."
        (folder / f"{name}.3mf").write_bytes(model_3mf)
        return f"Saved {name}.3mf to {folder}."

    gcode = slice_3mf(model_3mf, cfg)
    url = (cfg.get("url") or "").rstrip("/")
    if not url:
        raise MachineError("The printer address is empty.")
    start = bool(cfg.get("start_print"))
    if kind == "octoprint":
        body, ctype = _multipart({"select": "true", "print": str(start).lower()}, "file", f"{name}.gcode", gcode)
        _post(f"{url}/api/files/local", body, ctype, {"X-Api-Key": cfg.get("api_key", "")})
    elif kind == "moonraker":
        body, ctype = _multipart({"print": str(start).lower()}, "file", f"{name}.gcode", gcode)
        headers = {"X-Api-Key": cfg["api_key"]} if cfg.get("api_key") else {}
        _post(f"{url}/server/files/upload", body, ctype, headers)
    else:
        raise MachineError(f"Unknown printer kind '{kind}'.")
    return f"Sent {name}.gcode to the printer" + (" and started the print." if start else ". Start it from the printer.")


def open_with_default_app(path: Path) -> None:
    """Hand a file (the 3MF) to the person's own slicer."""
    if sys.platform == "win32":
        os.startfile(str(path))                       # type: ignore[attr-defined]
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
