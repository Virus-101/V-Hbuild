"""Idea in, a built device out - as a .vbuild file.

    plan (local model / Claude / offline)  ->  engine (pins, power, rules)  ->  replan on errors
      ->  firmware (+ compile)  ->  enclosure meshes  ->  BOM, wiring, guide  ->  .vbuild
"""
import csv
import io
import json

from . import compile as fwcompile
from . import engine, enclosure, firmware, geometry, llm, planner, vbuild
from .catalog import PASSIVES

AUTO = "auto"


def run(idea: str, provider=AUTO, compile_firmware: bool | None = None, log=print) -> dict:
    """provider: "auto", a provider name, a provider object, or None for offline."""
    if not idea or not idea.strip():
        raise ValueError("Describe the device first.")
    if isinstance(provider, str):
        provider = llm.choose(None if provider == AUTO else provider)

    if provider is not None:
        log(f"Planning with {provider.label}...")
        plan = planner.llm_plan(idea, provider)
        design = engine.build(plan)
        for _ in range(planner.MAX_REPLANS):
            objections = [i.message for i in design.issues if i.severity == "error" and i.replan]
            if not objections:
                break
            log(f"Engine rejected the plan ({len(objections)} problem(s)); replanning...")
            plan = planner.llm_plan(idea, provider, objections, previous=plan)
            design = engine.build(plan)
    else:
        log("No model available - planning offline by keyword.")
        plan = planner.offline_plan(idea)
        design = engine.build(plan)

    log("Writing firmware...")
    marked, lib_deps = firmware.skeleton(design)
    skeleton = firmware.finish(marked)
    ini = firmware.platformio_ini(design, lib_deps)
    main_cpp, fw_notes, model_notes = skeleton, [], []
    if provider is not None and design.ok:
        main_cpp, fw_notes, model_notes = firmware.llm_firmware(design, marked, provider)

    fw_file, fw_data = None, None
    if compile_firmware is None:
        compile_firmware = fwcompile.available()
    if compile_firmware and design.ok:
        log("Compiling firmware...")
        built = fwcompile.build(design.board, ini, main_cpp)
        if not built["ok"] and main_cpp != skeleton:
            # The model's code is the likelier culprit; the skeleton is known to build.
            fw_notes.append("The generated behaviour code did not compile, so the skeleton was "
                            "used instead. Compiler output: " + built["log"][-600:])
            main_cpp = skeleton
            built = fwcompile.build(design.board, ini, main_cpp)
        if built["ok"]:
            fw_file, fw_data = built["file"], built["data"]
        else:
            fw_notes.append("Firmware did not compile here: " + built["log"][-600:])
    elif main_cpp != skeleton:
        fw_notes.append("The behaviour code was not compile-checked: build firmware/ with PlatformIO before flashing.")

    log("Building the enclosure and the 3D model...")
    geo = geometry.build(design)
    text_files = {
        "README.md": assembly(design, plan, fw_notes, fw_file, model_notes),
        "bom.csv": bom_csv(design),
        "wiring.md": wiring_md(design),
        "design.json": json.dumps({"idea": idea, "plan": plan, "design": design.as_dict()}, indent=2),
        "firmware/platformio.ini": ini,
        "firmware/src/main.cpp": main_cpp,
        "enclosure/enclosure.scad": enclosure.scad(design),
    }
    binaries = {"model/assembly.glb": geo["assembly.glb"],
                "enclosure/print_plate.3mf": geo["print_plate.3mf"],
                "enclosure/shell.stl": geo["shell.stl"],
                "enclosure/lid.stl": geo["lid.stl"]}
    if fw_file:
        binaries[f"firmware/{fw_file}"] = fw_data

    rows = bom_rows(design)
    result = {
        "idea": idea,
        "planner": provider.label if provider is not None else "offline keyword planner",
        "plan": plan,
        "design": design.as_dict(),
        "bom": rows,
        "bom_total": round(sum(r["qty"] * r["unit_usd"] for r in rows), 2),
        "firmware_notes": fw_notes,
        "model_notes": model_notes,
        "firmware_binary": fw_file,
        "volume_cm3": geo["volume_cm3"],
        "files": text_files,
    }
    all_files = {**{k: v.encode() for k, v in text_files.items()}, **binaries}
    glb_nodes = set(geometry.load_glb(geo["assembly.glb"]).graph.nodes_geometry)
    man = vbuild.manifest(result, design, geo["box"], glb_nodes, all_files, fw_file)
    result["manifest"] = man
    result["_vbuild"] = vbuild.write(man, all_files)     # bytes; not sent as JSON
    return result


def bom_rows(d: engine.Design) -> list[dict]:
    rows = [{"ref": "U1", "part": d.board.name, "qty": 1, "unit_usd": d.board.price,
             "why": "microcontroller"}]
    for i in d.instances:
        rows.append({"ref": i.ref, "part": i.part.name, "qty": 1, "unit_usd": i.part.price,
                     "why": i.role})
    for pid, (qty, reason) in d.passives.items():
        name, price = PASSIVES[pid]
        rows.append({"ref": "-", "part": name, "qty": qty, "unit_usd": price, "why": reason})
    return rows


def bom_csv(d: engine.Design) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["ref", "part", "qty", "unit_usd", "why"])
    w.writeheader()
    w.writerows(bom_rows(d))
    return buf.getvalue()


def wiring_md(d: engine.Design) -> str:
    b = d.board
    out = [f"# Wiring - {d.name}", "",
           f"Board: **{b.name}**. I2C bus on GPIO{b.i2c[0]} (SDA) and GPIO{b.i2c[1]} (SCL); "
           "every I2C part shares those two wires.", "",
           "| From | To | Note |", "| --- | --- | --- |"]
    out += [f"| {a} | {t} | {n} |" for a, t, n in d.wires]
    i2c = [i for i in d.instances if i.address is not None]
    if i2c:
        out += ["", "## I2C addresses", "", "| Ref | Part | Address |", "| --- | --- | --- |"]
        out += [f"| {i.ref} | {i.part.name} | 0x{i.address:02X} |" for i in i2c]
    return "\n".join(out) + "\n"


def assembly(d: engine.Design, plan: dict, fw_notes: list[str], fw_file: str | None = None,
             model_notes: list[str] = ()) -> str:
    bud = d.budget
    out = [f"# {d.name}", "", d.summary, ""]
    problems = [i for i in d.issues if i.severity != "info"]
    if not d.ok:
        out += ["> **This design does not pass the electrical checks yet.** "
                "Fix the errors below before buying parts.", ""]
    if problems or any(i.severity == "info" for i in d.issues):
        out += ["## Checks", ""]
        out += [f"- **{i.severity}**: {i.message}" for i in d.issues]
        out.append("")
    for key, title in (("assumptions", "Assumptions"), ("out_of_scope", "Not covered")):
        if plan.get(key):
            out += [f"## {title}", ""] + [f"- {x}" for x in plan[key]] + [""]
    out += ["## Power", "",
            f"- Source: {bud.get('source')}",
            f"- 3.3 V rail peak: ~{bud.get('rail_3v3_peak_ma')} mA of {bud.get('rail_3v3_limit_ma')} mA",
            f"- 5 V rail peak: ~{bud.get('rail_5v_peak_ma')} mA; whole device ~{bud.get('total_peak_ma')} mA"]
    if "battery_hours" in bud:
        out.append(f"- Battery: ~{bud['battery_hours']} h at ~{bud['average_ma']} mA average (estimate)")
    out += ["", "## Build it", "",
            "1. Buy the parts in `bom.csv`.",
            "2. Wire it on a breadboard first, following `wiring.md` - power off while wiring.",
            "3. Install PlatformIO (VS Code extension or `pip install platformio`).",
            (f"4. Flash `firmware/{fw_file}` from the V-Hbuild desktop app, or "
             + ("copy it onto the RPI-RP2 drive (hold BOOTSEL while plugging in)."
                if fw_file.endswith(".uf2") else "`esptool.py write_flash 0x0 firmware/firmware.bin`.")
             if fw_file else
             "4. `cd firmware && pio run -t upload && pio device monitor` - "
             "you should see one JSON line of readings every cycle."),
            "5. Print `enclosure/print_plate.3mf` (shell and lid) - 0.2 mm layers, no supports, "
            "PLA indoors or PETG outdoors. The lid prints plate-down.",
            "6. Move the circuit to perfboard, mount it, close the lid."]
    if d.has("mt3608"):
        out.append("\nSet the MT3608 to 5.0 V with a multimeter **before** connecting anything to it.")
    if d.has("relay-1ch"):
        out.append("\nIf the relay switches mains, keep the mains wiring in its own sealed section "
                   "and have it checked by someone qualified.")
    if d.behavior:
        out += ["", "## What the firmware does", ""] + [f"- {b}" for b in d.behavior]
    if fw_notes:
        out += ["", "## Firmware notes", ""] + [f"- {n}" for n in fw_notes]
    if model_notes:
        out += ["", "## Notes from the model", "",
                "_The planner model's own words, not checked by V-Hbuild. The wiring, parts "
                "and code above are checked; these may mention things the design does not have._",
                ""] + [f"- {n}" for n in model_notes]
    return "\n".join(out) + "\n"


def bundle(result: dict) -> bytes:
    """The .vbuild file for a run."""
    return result["_vbuild"]


def filename(result: dict) -> str:
    stem = "".join(c if c.isalnum() else "-" for c in result["design"]["name"].lower()).strip("-")
    return (stem or "device") + ".vbuild"


__all__ = ["run", "bundle", "filename"]
