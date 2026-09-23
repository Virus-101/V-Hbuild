"""Compile the firmware into the one file the board takes.

ESP32 boards get a merged image (bootloader, partition table, boot_app0 and
the app in one .bin) written at offset 0x0, so any flasher - esptool, the
desktop app, a browser Web Serial flasher - needs one file and one address.
The Pico W gets the .uf2 its bootloader drive accepts by drag and drop.

Compiling needs PlatformIO. It is optional: without it the .vbuild carries
the source and says how to build it.
"""
import contextlib
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .catalog import Board

TIMEOUT = int(os.environ.get("FORGE_COMPILE_TIMEOUT", "900"))
# The app partition follows the Arduino-ESP32 default table.
ESP32_LAYOUT = (("0x0", "bootloader.bin"), ("0x8000", "partitions.bin"),
                ("0xe000", "boot_app0.bin"), ("0x10000", "firmware.bin"))


def pio() -> str | None:
    return shutil.which("pio") or shutil.which("platformio")


def available() -> bool:
    return pio() is not None


def _boot_app0() -> Path | None:
    core = Path(os.environ.get("PLATFORMIO_CORE_DIR", Path.home() / ".platformio"))
    p = core / "packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin"
    return p if p.exists() else None


def build(board: Board, platformio_ini: str, main_cpp: str) -> dict:
    """{ok, file, data, log} - file/data is the flashable image when ok."""
    exe = pio()
    if exe is None:
        return {"ok": False, "log": "PlatformIO is not installed."}
    with tempfile.TemporaryDirectory(prefix="forge-fw-") as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "platformio.ini").write_text(platformio_ini)
        (root / "src/main.cpp").write_text(main_cpp)
        try:
            run = subprocess.run([exe, "run", "-d", str(root)], capture_output=True,
                                 text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return {"ok": False, "log": f"Compile timed out after {TIMEOUT} s."}
        log = (run.stdout + run.stderr)[-4000:]
        if run.returncode != 0:
            return {"ok": False, "log": log}

        out = root / ".pio/build" / board.pio_env["board"]
        if board.chip == "rp2040":
            uf2 = out / "firmware.uf2"
            if not uf2.exists():
                return {"ok": False, "log": log + "\nNo firmware.uf2 was produced."}
            return {"ok": True, "file": "firmware.uf2", "data": uf2.read_bytes(), "log": log}

        boot_app0 = _boot_app0()
        parts = []
        for offset, name in ESP32_LAYOUT:
            path = boot_app0 if name == "boot_app0.bin" else out / name
            if path is None or not path.exists():
                return {"ok": False, "log": log + f"\nMissing {name} for the merged image."}
            parts += [offset, str(path)]
        merged = root / "firmware.bin"
        out_log = io.StringIO()
        try:
            import esptool
            with contextlib.redirect_stdout(out_log), contextlib.redirect_stderr(out_log):
                esptool.main(["--chip", board.chip, "merge-bin", "-o", str(merged),
                              "--flash-mode", "dio", "--flash-size", "4MB", *parts])
        except (SystemExit, Exception) as e:     # esptool exits or raises on bad input
            return {"ok": False, "log": log + f"\nCould not merge the image: {e}\n{out_log.getvalue()[-800:]}"}
        return {"ok": True, "file": "firmware.bin", "data": merged.read_bytes(), "log": log}
