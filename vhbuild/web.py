"""V-Hbuild server - the platform, and the engine behind the desktop app.

    python -m uvicorn vhbuild.web:app --host 127.0.0.1 --port 8780

Builds run as background jobs (a local model on a CPU can take a minute or two,
compiling another half minute), and the page polls their log.

Machine endpoints (flash a board, send to a printer, settings) exist only
when the desktop app starts the server: it sets DESKTOP_TOKEN, binds to
127.0.0.1, and every machine call must carry that token, so no web page the
person happens to have open can reach their USB ports.
"""
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from . import compile as fwcompile
from . import llm, machines, pipeline, vbuild

app = FastAPI(title="V-Hbuild")
STATIC = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

KEEP = 50
MAX_JOBS = int(os.environ.get("VHBUILD_MAX_JOBS", "2"))
# Builds waiting for a slot; past this a new build gets "busy" rather than a thread.
MAX_QUEUED = int(os.environ.get("VHBUILD_MAX_QUEUED", "20"))
# A generated .vbuild is a few hundred KB; uploads are capped well below the
# format's own limit so a public server cannot be filled from outside.
MAX_UPLOAD = int(os.environ.get("VHBUILD_MAX_UPLOAD_MB", "32")) * 1024 * 1024
MAX_HELD = int(os.environ.get("VHBUILD_MAX_HELD_MB", "512")) * 1024 * 1024
DESKTOP_TOKEN: str | None = None        # set by desktop.py

builds: "OrderedDict[str, dict]" = OrderedDict()    # id -> {"manifest", "files", "result"?}
jobs: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()
_slots = threading.Semaphore(MAX_JOBS)


def _remember(entry: dict) -> str:
    build_id = secrets.token_urlsafe(9)
    with _lock:
        builds[build_id] = entry
        # Drop the oldest builds past the count or the memory budget (never the new one).
        while len(builds) > 1 and (len(builds) > KEEP or
                                   sum(len(b["data"]) * 2 for b in builds.values()) > MAX_HELD):
            builds.popitem(last=False)
    return build_id


def _get(build_id: str) -> dict:
    entry = builds.get(build_id)
    if entry is None:
        raise HTTPException(404, "That build has expired - run it again or open the .vbuild file.")
    return entry


# --- pages -------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/viewer")
def viewer():
    return FileResponse(STATIC / "viewer.html")


@app.get("/api/status")
def status():
    return {"version": __version__, "models": llm.status(), "compiler": fwcompile.available(),
            "desktop": DESKTOP_TOKEN is not None}


# --- building ------------------------------------------------------------------

def _run_job(job_id: str, idea: str, provider: str) -> None:
    job = jobs[job_id]

    def log(msg: str) -> None:
        job["log"].append({"t": round(time.time() - job["started"], 1), "msg": msg})

    with _slots:
        job["state"] = "running"
        try:
            result = pipeline.run(idea, provider=None if provider == "offline" else provider, log=log)
        except llm.ProviderError as e:
            job.update(state="failed", error=str(e))
            return
        except Exception as e:      # an API or tool error should reach the page, not vanish
            job.update(state="failed", error=f"{type(e).__name__}: {e}")
            return
    _training_example(idea, result)
    data = pipeline.bundle(result)
    man, files = vbuild.read(data)
    build_id = _remember({"manifest": man, "files": files, "data": data,
                          "filename": pipeline.filename(result)})
    public = {k: v for k, v in result.items() if not k.startswith("_")}
    job.update(state="done", build_id=build_id, result={"id": build_id, **public})
    log("Done.")


def _training_example(idea: str, result: dict) -> None:
    """Keep plans that passed the engine as idea -> plan examples for fine-tuning a local model.

    Off unless VHBUILD_TRAINING_LOG names a file. Plans from a stronger model
    (Claude) that the engine accepted are the most useful ones to learn from.
    """
    path = os.environ.get("VHBUILD_TRAINING_LOG")
    if not path or not result["design"]["ok"] or result["planner"].startswith("offline"):
        return
    line = json.dumps({"idea": idea, "plan": result["plan"], "planner": result["planner"],
                       "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


@app.post("/api/build")
def build(body: dict = Body(...)):
    idea = str(body.get("idea") or "").strip()
    if not idea:
        raise HTTPException(400, "Describe the device first.")
    if len(idea) > 4000:
        raise HTTPException(400, "Keep the description under 4000 characters.")
    provider = str(body.get("provider") or "auto")
    if provider not in ("auto", "local", "claude", "offline"):
        raise HTTPException(400, "Unknown provider.")
    job_id = secrets.token_urlsafe(9)
    with _lock:
        waiting = sum(1 for j in jobs.values() if j["state"] in ("queued", "running"))
        if waiting >= MAX_JOBS + MAX_QUEUED:
            raise HTTPException(429, "V-Hbuild is busy with other builds. Try again in a minute.")
        jobs[job_id] = {"state": "queued", "log": [], "started": time.time()}
        while len(jobs) > KEEP:
            jobs.popitem(last=False)
    threading.Thread(target=_run_job, args=(job_id, idea, provider), daemon=True).start()
    return {"job": job_id}


@app.get("/api/jobs/{job_id}")
def job(job_id: str, since: int = 0):
    j = jobs.get(job_id)
    if j is None:
        raise HTTPException(404, "Unknown job.")
    out = {"state": j["state"], "log": j["log"][since:], "next": len(j["log"])}
    if j["state"] == "done":
        out["result"] = j["result"]
    if j["state"] == "failed":
        out["error"] = j["error"]
    return out


# --- .vbuild files -------------------------------------------------------------

@app.get("/api/build/{build_id}.vbuild")
def download(build_id: str):
    entry = _get(build_id)
    return Response(entry["data"], media_type="application/vnd.vhbuild.vbuild+zip",
                    headers={"Content-Disposition": f'attachment; filename="{entry["filename"]}"'})


@app.get("/api/build/{build_id}/file/{path:path}")
def build_file(build_id: str, path: str):
    """One file out of a build - for 'download the 3MF / firmware' links."""
    entry = _get(build_id)
    if path not in entry["files"]:
        raise HTTPException(404, "No such file in this build.")
    return Response(entry["files"][path], media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{Path(path).name}"'})


@app.post("/api/vbuild")
async def upload(request: Request):
    """Open a .vbuild someone already has (the viewer's drop zone)."""
    too_big = HTTPException(413, f"File too large (limit {MAX_UPLOAD // (1024 * 1024)} MB).")
    if int(request.headers.get("content-length") or 0) > MAX_UPLOAD:
        raise too_big
    chunks, size = [], 0
    async for chunk in request.stream():      # never hold more than the limit in memory
        size += len(chunk)
        if size > MAX_UPLOAD:
            raise too_big
        chunks.append(chunk)
    data = b"".join(chunks)
    try:
        man, files = vbuild.read(data)
    except vbuild.VbuildError as e:
        raise HTTPException(400, str(e))
    name = "".join(c if c.isalnum() else "-" for c in man.get("name", "device").lower()).strip("-")
    return {"id": _remember({"manifest": man, "files": files, "data": data,
                             "filename": f"{name or 'device'}.vbuild"}), "manifest": man}


# --- machines (desktop only) ---------------------------------------------------

def _desktop(request: Request) -> None:
    token = request.headers.get("x-vhbuild-token", "")
    if DESKTOP_TOKEN is None:
        raise HTTPException(404, "Machines are only available in the V-Hbuild desktop app.")
    if not hmac.compare_digest(token, DESKTOP_TOKEN):
        raise HTTPException(403, "Missing or wrong desktop token.")


@app.get("/api/machines")
def machine_list(request: Request):
    _desktop(request)
    return {"serial": machines.serial_ports(), "uf2": machines.uf2_drives(),
            "printer": machines.load_settings()["printer"]["kind"]}


@app.post("/api/machines/flash")
def machine_flash(request: Request, body: dict = Body(...)):
    _desktop(request)
    entry = _get(str(body.get("build") or ""))
    target = str(body.get("target") or "")
    if not target:
        raise HTTPException(400, "Choose the board's port (or its UF2 drive).")
    try:
        log = machines.flash(entry["manifest"], entry["files"], target)
    except machines.MachineError as e:
        return JSONResponse({"ok": False, "log": str(e)}, status_code=502)
    return {"ok": True, "log": log}


@app.post("/api/machines/print")
def machine_print(request: Request, body: dict = Body(...)):
    _desktop(request)
    entry = _get(str(body.get("build") or ""))
    plate = entry["files"].get("enclosure/print_plate.3mf")
    if plate is None:
        raise HTTPException(400, "This .vbuild has no print plate.")
    name = Path(entry["filename"]).stem
    cfg = machines.load_settings()["printer"]
    try:
        if body.get("open_in_slicer") or cfg["kind"] == "none":
            path = Path(tempfile.gettempdir()) / f"{name}.3mf"
            path.write_bytes(plate)
            machines.open_with_default_app(path)
            return {"ok": True, "log": f"Opened {path.name} in your slicer."}
        return {"ok": True, "log": machines.send_to_printer(name, plate, cfg)}
    except (machines.MachineError, OSError) as e:
        return JSONResponse({"ok": False, "log": str(e)}, status_code=502)


@app.get("/api/settings")
def get_settings(request: Request):
    _desktop(request)
    return machines.load_settings()


@app.post("/api/settings")
def set_settings(request: Request, body: dict = Body(...)):
    _desktop(request)
    s = machines.save_settings(body)
    apply_settings(s)
    return s


def apply_settings(s: dict) -> None:
    """Model choices live in settings.json on the desktop; the pipeline reads env."""
    os.environ["VHBUILD_PROVIDER"] = s.get("provider") or "auto"
    os.environ["VHBUILD_OLLAMA_URL"] = s.get("ollama_url") or "http://localhost:11434"
    os.environ["VHBUILD_LOCAL_MODEL"] = s.get("local_model") or "llama3.2:3b"
