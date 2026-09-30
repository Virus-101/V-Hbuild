"""V-Hbuild desktop app.

    python -m vhbuild.desktop                 open V-Hbuild
    python -m vhbuild.desktop device.vbuild   open a file straight into the viewer
                                            (what double-clicking a .vbuild runs)

Runs the same server as the platform, but on 127.0.0.1 at a free port with a
per-launch token, and with the machine endpoints switched on - this is the
copy of V-Hbuild that can reach the person's printer and USB ports. The window
is a native webview (pywebview); without one, the system browser is used.
"""
import argparse
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1).close()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("The V-Hbuild server did not start.")


def start_server(token: str, port: int):
    import uvicorn

    from vhbuild import machines, web
    web.DESKTOP_TOKEN = token
    web.apply_settings(machines.load_settings())
    config = uvicorn.Config(web.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    _wait(f"http://127.0.0.1:{port}/api/status")
    return server


def open_path(path: Path) -> str:
    """Load a .vbuild from disk into the running server; returns its build id."""
    from vhbuild import vbuild, web
    data = path.read_bytes()
    man, files = vbuild.read(data)
    return web._remember({"manifest": man, "files": files, "data": data, "filename": path.name})


def _log_to_file() -> None:
    """A windowed app has no console: sys.stdout is None and any print() would
    crash it. Send everything to a log file next to the settings instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from vhbuild import machines
    path = machines.settings_path().with_name("vhbuild.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(path, "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream


def main(argv=None) -> int:
    _log_to_file()
    ap = argparse.ArgumentParser(prog="vhbuild-desktop")
    ap.add_argument("file", nargs="?", help="a .vbuild to open")
    ap.add_argument("--browser", action="store_true", help="use the system browser instead of a window")
    ap.add_argument("--port", type=int, default=0, help="fixed local port (default: any free one)")
    args = ap.parse_args(argv)

    token, port = secrets.token_urlsafe(24), args.port or _free_port()
    server = start_server(token, port)
    base = f"http://127.0.0.1:{port}"
    url = f"{base}/?token={token}"
    if args.file:
        try:
            url = f"{base}/viewer?build={open_path(Path(args.file))}&token={token}"
        except (OSError, ValueError) as e:
            print(f"Could not open {args.file}: {e}", file=sys.stderr, flush=True)

    webview = None
    if not args.browser:
        try:
            import webview  # pywebview
        except ImportError:
            webview = None
    if webview is not None:
        webview.create_window("V-Hbuild", url, width=1320, height=880, min_size=(900, 600))
        webview.start()
    else:
        import webbrowser
        webbrowser.open(url)
        print(f"V-Hbuild is running at {base} - close this window to quit.", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    server.should_exit = True
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    sys.exit(main())
