"""Forge: engine rules, offline pipeline, and the Claude path with a fake client."""
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from forge import engine, enclosure, firmware, pipeline, planner
from forge.catalog import BOARDS, PARTS


def plan(*part_ids, board="esp32-c3-devkitm-1", power="usb", low_power=False):
    return {"name": "Test", "summary": "", "board_id": board, "power": power,
            "low_power": low_power, "behavior": [],
            "parts": [{"part_id": p, "role": p} for p in part_ids]}


def errors(d):
    return [i for i in d.issues if i.severity == "error"]


# --- engine -------------------------------------------------------------

def test_every_board_pin_list_is_clean():
    for b in BOARDS.values():
        assert len(set(b.digital)) == len(b.digital), b.id
        assert set(b.i2c).isdisjoint(b.analog), b.id


def test_i2c_parts_share_the_bus_and_get_distinct_addresses():
    d = engine.build(plan("bme280", "bme280", "ssd1306"))
    addrs = [i.address for i in d.instances if i.address is not None]
    assert sorted(addrs) == [0x3C, 0x76, 0x77]
    assert all(i.pins == {"SDA": 8, "SCL": 9} for i in d.instances if i.address)
    assert any("0x77" in i.message for i in d.issues)     # strap instruction given
    assert d.ok


def test_i2c_clash_with_no_alternative_is_an_error_to_replan():
    d = engine.build(plan("vl53l0x", "vl53l0x"))
    errs = errors(d)
    assert len(errs) == 1 and errs[0].replan
    assert not d.ok


def test_gpio_never_reused_and_analog_on_adc_pins():
    d = engine.build(plan("soil-capacitive", "button", "buzzer", "hcsr04", "pir-hcsr501"))
    used = [p for i in d.instances if i.part.interface != "i2c" for p in i.pins.values()]
    assert len(used) == len(set(used))
    soil = d.parts("soil-capacitive")[0]
    assert soil.pins["AOUT"] in d.board.analog


def test_out_of_pins_moves_to_a_bigger_board():
    d = engine.build(plan(*(["button"] * 12)))
    assert d.board.id != "esp32-c3-devkitm-1"
    assert any("switched to" in i.message for i in d.issues)
    assert d.ok


def test_no_board_fits_is_an_error():
    d = engine.build(plan(*(["button"] * 40)))
    assert not d.ok and errors(d)[0].replan


def test_5v_logic_gets_a_divider_and_onewire_a_pullup():
    d = engine.build(plan("hcsr04", "ds18b20"))
    assert {"r1k", "r2k", "r4k7"} <= set(d.passives)
    assert any("divider" in note for _, _, note in d.wires)


def test_heavy_5v_load_on_usb_moves_to_an_adapter():
    d = engine.build(plan("sg90", "ws2812b-ring12"))
    assert d.power == "usb_adapter"
    assert d.has("psu-5v2a")
    assert "c1000u" in d.passives


def test_pump_brings_its_relay_and_is_switched_through_it():
    d = engine.build(plan("pump-5v", "soil-capacitive"))
    assert d.has("relay-1ch")
    relay = d.parts("relay-1ch")[0]
    assert any(t == f"{relay.ref} NO" for _, t, _ in d.wires)


def test_lipo_adds_charger_and_boost_for_5v_parts():
    d = engine.build(plan("pir-hcsr501", power="lipo"))
    assert d.has("lipo-2000") and d.has("tp4056") and d.has("mt3608")
    assert "battery_hours" in d.budget


def test_pico_on_lipo_runs_from_vsys_without_a_boost():
    d = engine.build(plan("sht31", board="rpi-pico-w", power="lipo", low_power=True))
    assert not d.has("mt3608")
    assert any(t == "board VSYS pin" for _, t, _ in d.wires)


def test_battery_too_weak_for_the_load():
    d = engine.build(plan("sg90", "sg90", "ws2812b-ring12", "ws2812b-ring12", power="lipo"))
    assert any("LiPo" in i.message for i in errors(d))


def test_unknown_part_is_reported_not_crashed():
    d = engine.build(plan("flux-capacitor"))
    assert not d.ok


# --- outputs ------------------------------------------------------------

def test_firmware_declares_every_allocated_pin():
    d = engine.build(plan("hcsr04", "button", "relay-1ch", "ds18b20", "ssd1306"))
    cpp, deps = firmware.skeleton(d)
    for inst in d.instances:
        if inst.part.interface in ("i2c", "power") or not inst.rail:
            continue
        for sig, pin in inst.pins.items():
            assert f"const int {inst.ref}_{sig} = {pin};" in cpp
    assert "Wire.begin(8, 9);" in cpp
    assert "adafruit/Adafruit SSD1306" in deps
    assert cpp.count("{") == cpp.count("}")


def test_pico_firmware_uses_its_own_i2c_setup_and_servo_lib():
    d = engine.build(plan("sg90", "bme280", board="rpi-pico-w", power="usb_adapter"))
    cpp, deps = firmware.skeleton(d)
    assert "Wire.setSDA(4);" in cpp and "#include <Servo.h>" in cpp
    assert "madhephaestus/ESP32Servo" not in deps
    assert "earlephilhower" in firmware.platformio_ini(d, deps)


def test_low_power_esp32_deep_sleeps():
    d = engine.build(plan("bme280", power="lipo", low_power=True))
    cpp, _ = firmware.skeleton(d)
    assert "esp_deep_sleep_start();" in cpp


def test_enclosure_fits_every_inside_part():
    d = engine.build(plan("ssd1306", "pir-hcsr501", "soil-capacitive", "button"))
    placed, (L, W, H) = enclosure.layout(d)
    refs = {p.ref for p in placed}
    assert "U1" in refs
    assert not {i.ref for i in d.parts("soil-capacitive")} & refs     # the probe lives outside
    for p in placed:
        assert p.x + p.w <= L and p.y + p.d <= W and p.h < H
    for a in placed:
        for b in placed:
            if a is not b:
                apart = (a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.d <= b.y or b.y + b.d <= a.y)
                assert apart, (a.ref, b.ref)
    scad = enclosure.scad(d)
    assert "cylinder(d = 23.5" in scad        # PIR dome
    assert scad.count("{") == scad.count("}")


def test_offline_pipeline_end_to_end():
    r = pipeline.run("A plant monitor that waters itself and shows moisture on a screen",
                     provider=None, compile_firmware=False, log=lambda m: None)
    ids = {i["part_id"] for i in r["design"]["instances"]}
    assert {"soil-capacitive", "pump-5v", "relay-1ch", "ssd1306"} <= ids
    assert r["bom_total"] > 0
    names = zipfile.ZipFile(io.BytesIO(pipeline.bundle(r))).namelist()
    assert {"manifest.json", "firmware/src/main.cpp", "enclosure/enclosure.scad",
            "model/assembly.glb", "enclosure/print_plate.3mf"} <= set(names)


def test_offline_keywords_do_not_fire_on_substrings():
    p = planner.offline_plan("waterproof weather station measuring pressure")
    ids = {x["part_id"] for x in p["parts"]}
    assert "pump-5v" not in ids and "button" not in ids and "bme280" in ids


# --- models, faked -------------------------------------------------------

class FakeProvider:
    """Stands in for the local model or Claude: returns queued outputs in order."""
    label = "fake"

    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []

    def structured(self, system, user, out, max_tokens=0):
        self.calls.append({"system": system, "user": user, "out": out})
        o = self.outputs.pop(0)
        return o(user, out) if callable(o) else o


def as_plan(p):
    return planner.Plan(**{**p, "assumptions": [], "out_of_scope": []})


def rules(*rules, notes=()):
    """A behaviour answer, built with the per-device schema the pipeline asks for."""
    return lambda user, out: out.model_validate({"rules": list(rules), "notes": list(notes)})


echo_firmware = rules({"every_seconds": 10, "variable": "always", "compare": "none", "threshold": 0,
                       "then": [{"do": "serial.print", "text": "alive"}]}, notes=["tune thresholds"])


def test_plan_is_replanned_when_the_engine_objects():
    bad = as_plan(plan("vl53l0x", "vl53l0x"))
    good = as_plan(plan("vl53l0x", "hcsr04"))
    fake = FakeProvider([bad, good, echo_firmware])
    r = pipeline.run("measure two distances", provider=fake, compile_firmware=False, log=lambda m: None)
    assert r["design"]["ok"]
    assert "engine rejected" in fake.calls[1]["user"]
    assert r["model_notes"] == ["tune thresholds"]
    assert r["planner"] == "fake"


def test_rules_become_code_in_the_skeleton():
    good = as_plan(plan("soil-capacitive", "pump-5v", "ssd1306"))
    water = {"every_seconds": 600, "variable": "s1_pct", "compare": "<", "threshold": 35,
             "then": [{"do": "M2.switch_on_for", "amount": 3},
                      {"do": "DS1.show", "text": 'Watered "now"'}]}
    fake = FakeProvider([good, rules(water, notes=["35% is a starting point"])])
    r = pipeline.run("water my basil when the soil is dry, show it on a screen", provider=fake,
                     compile_firmware=False, log=lambda m: None)
    cpp = r["files"]["firmware/src/main.cpp"]
    assert "const unsigned long RULE1_EVERY_MS = 600000UL;" in cpp
    assert "if (s1_pct < 35) { m2_set(true); delay(3000); m2_set(false); " in cpp
    assert 'ds1.print("Watered \\"now\\""); ds1.println(); ds1.display();' in cpp   # escaped C
    assert cpp.index("RULE1_EVERY_MS =") < cpp.index("void setup") < cpp.index("rule1Last = millis()")
    assert "FORGE:" not in cpp and r["model_notes"] == ["35% is a starting point"]
    assert "## Notes from the model" in r["files"]["README.md"]
    assert "s1_pct" in fake.calls[1]["user"] and "M2.switch_on_for" in fake.calls[1]["user"]


def test_rule_schema_only_offers_what_the_device_has():
    d = engine.build(plan("button", "buzzer"))
    B = firmware.behaviour_model(d)
    ok = {"every_seconds": 0, "variable": "sw1_pressed", "compare": "is true", "threshold": 0,
          "then": [{"do": "L1.beep", "amount": 0.2}]}
    assert B.model_validate({"rules": [ok], "notes": []})
    with pytest.raises(Exception):   # there is no pump on this device
        B.model_validate({"rules": [{**ok, "then": [{"do": "M2.switch_on"}]}], "notes": []})
    const, code = firmware.rules_to_code(d, B.model_validate({"rules": [ok], "notes": []}))
    assert code == "if (sw1_pressed) { l1_beep(200); }" and const == ""


def test_hostile_text_cannot_escape_the_string():
    d = engine.build(plan("ssd1306"))
    B = firmware.behaviour_model(d)
    evil = {"every_seconds": 0, "variable": "always", "compare": "none", "threshold": 0,
            "then": [{"do": "DS1.show", "text": '"); pinMode(4, OUTPUT); ("'}]}
    _, code = firmware.rules_to_code(d, B.model_validate({"rules": [evil], "notes": []}))
    assert code.count('"') - code.count('\\"') == 2       # one string literal, quotes inside escaped
    assert firmware._c_string("21°C ✓") == '"21 degC "'


def test_messages_print_live_values_and_numbers_are_not_conditions():
    d = engine.build(plan("bme280", "ssd1306"))
    B = firmware.behaviour_model(d)
    r = {"every_seconds": 10, "variable": "s1_temp", "compare": "is true", "threshold": 0,
         "then": [{"do": "serial.print", "text": "Temp: {s1_temp} C {nope}"},
                  {"do": "DS1.show", "text": "s1_hum"}]}
    _, code = firmware.rules_to_code(d, B.model_validate({"rules": [r], "notes": []}))
    assert "if (true) {" in code
    assert 'Serial.print("Temp: "); Serial.print(s1_temp); Serial.print(" C "); Serial.print("{nope}"); Serial.println();' in code
    assert "ds1.print(s1_hum); ds1.println(); ds1.display();" in code


def test_firmware_that_does_not_compile_falls_back_to_the_skeleton(monkeypatch):
    from forge import compile as fwcompile
    good = as_plan(plan("button"))
    broken = rules({"every_seconds": 0, "variable": "always", "compare": "none", "threshold": 0,
                    "then": [{"do": "serial.print", "text": "hi"}]})
    calls = []

    def fake_build(board, ini, cpp):
        calls.append(cpp)
        return ({"ok": False, "log": "error: expected ';'"} if "Serial.print(\"hi\")" in cpp
                else {"ok": True, "file": "firmware.bin", "data": b"\xe9fake"})
    monkeypatch.setattr(fwcompile, "build", fake_build)
    r = pipeline.run("a button", provider=FakeProvider([good, broken]), compile_firmware=True, log=lambda m: None)
    assert len(calls) == 2 and "Serial.print(\"hi\")" not in r["files"]["firmware/src/main.cpp"]
    assert r["firmware_binary"] == "firmware.bin"
    assert any("did not compile" in n for n in r["firmware_notes"])


def test_plan_schema_only_allows_catalog_parts():
    with pytest.raises(Exception):
        planner.PartChoice(part_id="flux-capacitor", role="x")
    assert planner.PartChoice(part_id="bme280", role="x")
    assert set(PARTS) >= {"bme280"}


# --- local model (Ollama) ---------------------------------------------------------

def plan_json(**over):
    return as_plan({**plan("sht31", "ssd1306"), **over}).model_dump_json()


def test_local_model_sends_a_flat_schema_with_catalog_enums():
    from forge import llm
    sent = []

    def post(path, payload):
        sent.append((path, payload))
        return {"message": {"content": plan_json()}}
    mv = llm.Local(url="http://ollama:11434", model="llama3.2:3b", post=post)
    p = planner.llm_plan("a thermometer with a screen", mv)
    assert p["parts"][0]["part_id"] == "sht31"
    path, payload = sent[0]
    assert path == "/api/chat" and payload["model"] == "llama3.2:3b" and payload["stream"] is False
    schema = json.dumps(payload["format"])
    assert "$ref" not in schema and "$defs" not in schema
    assert '"bme280"' in schema and '"esp32-c3-devkitm-1"' in schema     # enums reached the grammar


def test_local_model_retries_once_with_the_validation_error():
    from forge import llm
    replies = iter(['{"name": "x"}', plan_json()])
    seen = []

    def post(path, payload):
        seen.append(payload["messages"])
        return {"message": {"content": next(replies)}}
    p = planner.llm_plan("idea", llm.Local(post=post))
    assert p["board_id"] == "esp32-c3-devkitm-1"
    assert "did not match the schema" in seen[1][-1]["content"]


def test_local_model_gives_up_after_two_bad_answers():
    from forge import llm
    mv = llm.Local(post=lambda path, payload: {"message": {"content": "not json"}})
    with pytest.raises(llm.ProviderError):
        planner.llm_plan("idea", mv)


def test_local_model_falls_back_to_cpu_when_the_gpu_runner_dies():
    import urllib.error
    from forge import llm
    payloads = []

    def post(path, payload):
        payloads.append(payload)
        if len(payloads) == 1:
            raise urllib.error.URLError("connection reset")
        return {"message": {"content": plan_json()}}
    planner.llm_plan("idea", llm.Local(post=post))
    assert "num_gpu" not in payloads[0]["options"] and payloads[1]["options"]["num_gpu"] == 0


def test_claude_provider_uses_structured_outputs_and_fallbacks():
    from forge import llm

    class Client:
        def __init__(self):
            self.kw = None
            self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

        def parse(self, **kw):
            self.kw = kw
            return SimpleNamespace(stop_reason="end_turn", parsed_output=as_plan(plan("sht31")))
    client = Client()
    planner.llm_plan("idea", llm.Claude(client=client))
    assert client.kw["output_format"] is planner.Plan and client.kw["fallbacks"] == "default"


def test_choose_prefers_the_local_model_and_respects_offline(monkeypatch):
    from forge import llm
    monkeypatch.delenv("FORGE_OFFLINE", raising=False)
    monkeypatch.setattr(llm.Local, "available", lambda self: True)
    assert isinstance(llm.choose(), llm.Local)
    assert llm.choose("offline") is None
    monkeypatch.setattr(llm.Local, "available", lambda self: False)
    monkeypatch.setattr(llm.Claude, "available", staticmethod(lambda: False))
    assert llm.choose() is None


# --- geometry -------------------------------------------------------------------

def test_meshes_are_watertight_and_the_glb_names_every_part():
    from forge import geometry
    d = engine.build(plan("ssd1306", "pir-hcsr501", "button", "sht31", "ds18b20"))
    g = geometry.build(d)
    assert g["watertight"] and g["volume_cm3"] > 10
    names = set(geometry.load_glb(g["assembly.glb"]).graph.nodes_geometry)
    assert {"shell", "lid", "U1"} | {i.ref for i in d.instances if i.part.id != "psu-5v2a"} <= names


def test_lid_holes_line_up_with_their_parts_after_the_flip():
    from manifold3d import Manifold
    from forge import geometry
    d = engine.build(plan("pir-hcsr501", "button", "sht31", "bh1750"))
    b = enclosure.box(d)
    placed_lid = geometry.lid_in_place(b, geometry.lid(b))
    Ho = b.outer[2]
    for p in b.placed:
        if not p.on_lid:
            continue
        cx, cy = enclosure.WALL + p.x + p.w / 2, enclosure.WALL + p.y + p.d / 2
        probe = Manifold.cube([2, 2, enclosure.WALL]).translate([cx - 1, cy - 1, Ho])
        assert (placed_lid ^ probe).volume() < 1e-6, f"{p.ref}: lid is solid over its opening"
    # and somewhere with no part, the lid is solid
    probe = Manifold.cube([2, 2, enclosure.WALL]).translate([b.outer[0] - 3, b.outer[1] - 3, Ho])
    assert (placed_lid ^ probe).volume() > 7


def test_scad_and_meshes_come_from_the_same_cuts():
    d = engine.build(plan("pir-hcsr501", "sht31", "ds18b20"))
    b = enclosure.box(d)
    scad = enclosure.scad(d)
    assert scad.count("// cable gland") == sum(1 for c in b.cuts if "cable gland" in c.note)
    assert "mirror([0, 1, 0])" in scad


# --- the .vbuild file -----------------------------------------------------------

def offline(idea):
    return pipeline.run(idea, provider=None, compile_firmware=False, log=lambda m: None)


def test_vbuild_round_trips_and_describes_its_machines():
    from forge import vbuild
    r = offline("a motion alarm with a buzzer and a screen")
    man, files = vbuild.read(pipeline.bundle(r))
    assert man["format"] == "vbuild" and man["version"] == 1
    assert man["model"] in files and "enclosure/print_plate.3mf" in files
    assert set(man["files"]) == set(files)
    assert man["steps"][0]["action"] == "print" and man["steps"][-1]["action"] == "close"
    shown = {n for s in man["steps"] for n in s.get("show", [])}
    assert {"shell", "lid", "U1"} <= shown
    assert man["machines"]["board"]["file"] is None       # compile was off
    assert all(len(w["points"]) == 4 for w in man["wires"])


def test_vbuild_rejects_damage_and_unsafe_paths():
    import zipfile
    from forge import vbuild
    data = pipeline.bundle(offline("a button"))
    z = zipfile.ZipFile(io.BytesIO(data))
    parts = {n: z.read(n) for n in z.namelist()}

    def rezip(entries):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as out:
            for n, b in entries.items():
                out.writestr(n, b)
        return buf.getvalue()
    tampered = {**parts, "bom.csv": b"evil"}
    with pytest.raises(vbuild.VbuildError, match="damaged"):
        vbuild.read(rezip(tampered))
    with pytest.raises(vbuild.VbuildError, match="Unsafe"):
        vbuild.read(rezip({**parts, "../escape.txt": b"x"}))
    newer = json.loads(parts["manifest.json"]); newer["version"] = 99
    with pytest.raises(vbuild.VbuildError, match="newer"):
        vbuild.read(rezip({**parts, "manifest.json": json.dumps(newer).encode()}))
    with pytest.raises(vbuild.VbuildError):
        vbuild.read(b"not a zip")


# --- machines -------------------------------------------------------------------

def test_uf2_drive_is_found_and_flashed(tmp_path):
    from forge import machines
    drive = tmp_path / "RPI-RP2"
    drive.mkdir()
    (drive / "INFO_UF2.TXT").write_text("UF2 Bootloader v3.0\nModel: Raspberry Pi RP2\nBoard-ID: RPI-RP2\n")
    assert machines.uf2_drives([drive, tmp_path]) == [{"path": str(drive), "board": "RPI-RP2"}]
    man = {"machines": {"board": {"file": "firmware/firmware.uf2", "method": "uf2-drive"}}}
    machines.flash(man, {"firmware/firmware.uf2": b"UF2\n" * 10}, str(drive))
    assert (drive / "firmware.uf2").read_bytes().startswith(b"UF2")
    with pytest.raises(machines.MachineError):
        machines.flash_uf2(str(tmp_path), b"x")


def test_flash_needs_compiled_firmware():
    from forge import machines
    with pytest.raises(machines.MachineError, match="no compiled firmware"):
        machines.flash({"machines": {"board": {"file": None}}}, {}, "COM3")


def test_esp32_flash_calls_esptool_with_the_merged_image(monkeypatch):
    import esptool
    from forge import machines
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        seen["image"] = open(argv[-1], "rb").read()
    monkeypatch.setattr(esptool, "main", fake_main)
    man = {"machines": {"board": {"file": "firmware/firmware.bin", "method": "esptool",
                                  "chip": "esp32c3", "offset": "0x0"}}}
    machines.flash(man, {"firmware/firmware.bin": b"\xe9image"}, "/dev/ttyACM0")
    assert seen["argv"][:4] == ["--chip", "esp32c3", "--port", "/dev/ttyACM0"]
    assert "write-flash" in seen["argv"] and seen["argv"][-2] == "0x0" and seen["image"] == b"\xe9image"


@pytest.fixture
def printer_server():
    """A local HTTP server that records uploads, standing in for OctoPrint / Moonraker."""
    import http.server
    import threading
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.append({"path": self.path, "headers": dict(self.headers), "body": body})
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"done": true}')

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", got
    srv.shutdown()


@pytest.fixture
def fake_slicer(tmp_path):
    """A 'slicer' that turns the 3MF into G-code, PrusaSlicer-CLI style."""
    import sys
    script = tmp_path / "slicer.py"
    script.write_text("import sys\nargs = sys.argv[1:]\nout = args[args.index('-o') + 1]\n"
                      "open(out, 'w').write('; sliced ' + args[-1] + '\\nG28\\n')\n")
    return [sys.executable, str(script)]


def test_octoprint_and_moonraker_receive_sliced_gcode(printer_server, fake_slicer, monkeypatch):
    from forge import machines
    url, got = printer_server
    real_run = machines.subprocess.run
    monkeypatch.setattr(machines.subprocess, "run",
                        lambda cmd, **kw: real_run([*fake_slicer, *cmd[1:]], **kw))
    base = {"slicer": "prusa-slicer", "profile": "", "slicer_args": "--export-gcode --load {profile} -o {out} {in}",
            "url": url, "api_key": "k3y", "start_print": True}
    msg = machines.send_to_printer("lamp", b"3mf-bytes", {**base, "kind": "octoprint"})
    assert "started" in msg
    assert got[0]["path"] == "/api/files/local" and got[0]["headers"]["X-Api-Key"] == "k3y"
    assert b'filename="lamp.gcode"' in got[0]["body"] and b"G28" in got[0]["body"]
    assert b'name="print"\r\n\r\ntrue' in got[0]["body"]
    machines.send_to_printer("lamp", b"3mf-bytes", {**base, "kind": "moonraker"})
    assert got[1]["path"] == "/server/files/upload"


def test_printer_folder_and_unreachable_printer(tmp_path):
    from forge import machines
    msg = machines.send_to_printer("lamp", b"3mf", {"kind": "folder", "folder": str(tmp_path), "slicer": ""})
    assert (tmp_path / "lamp.3mf").read_bytes() == b"3mf" and "Saved" in msg
    with pytest.raises(machines.MachineError, match="No printer"):
        machines.send_to_printer("lamp", b"3mf", {"kind": "none"})


def test_settings_round_trip(tmp_path, monkeypatch):
    from forge import machines
    monkeypatch.setenv("FORGE_SETTINGS", str(tmp_path / "s.json"))
    assert machines.load_settings()["printer"]["kind"] == "none"
    machines.save_settings({"local_model": "qwen2.5:3b", "printer": {"kind": "moonraker", "bogus": 1}, "junk": 2})
    s = machines.load_settings()
    assert s["local_model"] == "qwen2.5:3b" and s["printer"]["kind"] == "moonraker"
    assert "bogus" not in s["printer"] and "junk" not in s


# --- web and desktop ------------------------------------------------------------

def wait_job(c, job):
    import time
    for _ in range(600):
        j = c.get(f"/api/jobs/{job}").json()
        if j["state"] in ("done", "failed"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_web_job_build_download_and_upload(monkeypatch):
    from fastapi.testclient import TestClient
    from forge import compile as fwcompile
    from forge import vbuild, web
    monkeypatch.setattr(fwcompile, "available", lambda: False)
    c = TestClient(web.app)
    assert c.get("/api/status").json()["desktop"] is False
    assert c.post("/api/build", json={"idea": ""}).status_code == 400
    job = c.post("/api/build", json={"idea": "a motion alarm with a buzzer", "provider": "offline"}).json()["job"]
    j = wait_job(c, job)
    assert j["state"] == "done", j.get("error")
    assert any("Done" in line["msg"] for line in j["log"])
    bid = j["result"]["id"]
    data = c.get(f"/api/build/{bid}.vbuild").content
    man, _ = vbuild.read(data)
    assert c.get(f"/api/build/{bid}/file/enclosure/print_plate.3mf").status_code == 200
    assert c.get(f"/api/build/{bid}/file/../../etc/passwd").status_code == 404
    up = c.post("/api/vbuild", content=data).json()
    assert up["manifest"]["name"] == man["name"]
    assert c.post("/api/vbuild", content=b"junk").status_code == 400
    assert c.get("/viewer").status_code == 200
    assert c.get("/static/vendor/three.module.min.js").status_code == 200


def test_machine_endpoints_are_desktop_only_and_need_the_token(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from forge import web
    monkeypatch.setenv("FORGE_SETTINGS", str(tmp_path / "s.json"))
    c = TestClient(web.app)
    monkeypatch.setattr(web, "DESKTOP_TOKEN", None)
    assert c.get("/api/machines").status_code == 404
    monkeypatch.setattr(web, "DESKTOP_TOKEN", "secret")
    assert c.get("/api/machines").status_code == 403
    assert c.get("/api/machines", headers={"X-Forge-Token": "wrong"}).status_code == 403
    ok = c.get("/api/machines", headers={"X-Forge-Token": "secret"})
    assert ok.status_code == 200 and set(ok.json()) == {"serial", "uf2", "printer"}
    assert c.post("/api/machines/flash", json={"build": "nope", "target": "x"},
                  headers={"X-Forge-Token": "secret"}).status_code == 404


def test_desktop_opens_a_vbuild_from_disk(tmp_path):
    from forge import desktop, web
    path = tmp_path / "lamp.vbuild"
    path.write_bytes(pipeline.bundle(offline("a lamp with a button")))
    bid = desktop.open_path(path)
    assert web.builds[bid]["manifest"]["format"] == "vbuild"


@pytest.mark.skipif(not __import__("os").environ.get("FORGE_TEST_COMPILE"),
                    reason="set FORGE_TEST_COMPILE=1 (needs PlatformIO; slow the first time)")
@pytest.mark.parametrize("board", ["esp32-c3-devkitm-1", "rpi-pico-w"])
def test_real_compile_produces_a_flashable_image(board):
    from forge import compile as fwcompile
    d = engine.build(plan("bme280", "button", "ssd1306", board=board))
    cpp, deps = firmware.skeleton(d)
    ini = firmware.platformio_ini(d, deps)
    built = fwcompile.build(d.board, ini, cpp)
    assert built["ok"], built["log"][-1500:]
    # Built again from the cached project, it must still produce the image.
    again = fwcompile.build(d.board, ini, cpp)
    assert again["ok"] and again["data"] == built["data"], again["log"][-1500:]
    if board.startswith("esp32"):
        assert built["file"] == "firmware.bin" and built["data"][0] == 0xE9     # ESP image magic at 0x0
    else:
        assert built["file"] == "firmware.uf2" and built["data"][:4] == b"UF2\n"


def test_accepted_model_plans_are_logged_for_training(tmp_path, monkeypatch):
    from forge import web
    log = tmp_path / "train.jsonl"
    monkeypatch.setenv("FORGE_TRAINING_LOG", str(log))
    good = {"design": {"ok": True}, "planner": "Claude (claude-opus-5)", "plan": {"parts": []}}
    web._training_example("a lamp", good)
    web._training_example("a lamp", {**good, "planner": "offline keyword planner"})
    web._training_example("a lamp", {**good, "design": {"ok": False}})
    lines = log.read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["idea"] == "a lamp"



def test_review_strips_what_the_idea_did_not_ask_for():
    # A padded plan of the kind a small local model produces for this idea.
    idea = "A plant monitor that waters my basil when the soil is dry and shows the moisture on a small screen"
    padded = {**plan("soil-capacitive", "bme280", "sht31", "button", "ssd1306", "pump-5v",
                     "relay-1ch", "sg90", "buzzer", board="esp32-s3-devkitc-1", low_power=True),
              "behavior": ["If soil is below 35% run the pump for 3 s", "When the button is pressed stop",
                           "Sound the alarm when it is too hot"],
              "assumptions": [], "out_of_scope": ["Camera", "GPS", "Cellular"]}
    r = planner.review(idea, padded)
    assert [p["part_id"] for p in r["parts"]] == ["soil-capacitive", "bme280", "ssd1306", "pump-5v", "relay-1ch"]
    assert r["low_power"] is False and r["out_of_scope"] == []
    assert r["behavior"] == ["If soil is below 35% run the pump for 3 s"]
    assert sum("Removed" in a for a in r["assumptions"]) == 4


def test_review_keeps_what_was_asked_for_and_never_empties_a_plan():
    kept = planner.review("a doorbell button that beeps a buzzer", {**plan("button", "buzzer"),
                          "assumptions": [], "out_of_scope": []})
    assert [p["part_id"] for p in kept["parts"]] == ["button", "buzzer"]
    alone = {**plan("sg90"), "assumptions": [], "out_of_scope": []}
    assert planner.review("something vague", alone)["parts"] == alone["parts"]
    two = planner.review("two buttons", {**plan("button", "button"), "assumptions": [], "out_of_scope": []})
    assert len(two["parts"]) == 2


# --- fixes in 0.2.0 -----------------------------------------------------------

def test_a_plan_with_no_parts_is_rejected_for_a_replan():
    d = engine.build(plan())
    assert not d.ok and errors(d)[0].replan and "no parts" in errors(d)[0].message


def test_c3_serial_goes_to_the_usb_bridge():
    # The DevKitM-1's micro-USB is a CP2102N; CDC-on-boot would silence Serial there.
    assert "CDC_ON_BOOT" not in firmware.platformio_ini(engine.build(plan("button")), [])


def http_error(code):
    import urllib.error
    return urllib.error.HTTPError("http://ollama/api/chat", code, "x", {}, io.BytesIO(b"{}"))


def test_local_model_errors_say_what_to_do():
    import urllib.error
    from forge import llm

    def raising(exc):
        calls = []

        def post(path, payload):
            calls.append(payload)
            raise exc
        return post, calls
    post, calls = raising(http_error(404))
    with pytest.raises(llm.ProviderError, match="ollama pull llama3.2:3b"):
        planner.llm_plan("idea", llm.Local(model="llama3.2:3b", post=post))
    assert len(calls) == 1                                  # no pointless CPU retry
    post, calls = raising(urllib.error.URLError(ConnectionRefusedError()))
    with pytest.raises(llm.ProviderError, match="not running"):
        planner.llm_plan("idea", llm.Local(post=post))
    post, calls = raising(TimeoutError())
    with pytest.raises(llm.ProviderError, match="longer than"):
        planner.llm_plan("idea", llm.Local(post=post))
    assert len(calls) == 1
    post, calls = raising(http_error(500))
    with pytest.raises(llm.ProviderError):
        planner.llm_plan("idea", llm.Local(post=post))
    assert len(calls) == 2 and calls[1]["options"]["num_gpu"] == 0   # 500 = crashed runner: CPU once


def test_messages_can_show_dotted_readings():
    d = engine.build(plan("mpu6050"))
    B = firmware.behaviour_model(d)
    r = {"every_seconds": 0, "variable": "always", "compare": "none", "threshold": 0,
         "then": [{"do": "serial.print", "text": "x={s1_a.acceleration.x}"}]}
    _, code = firmware.rules_to_code(d, B.model_validate({"rules": [r], "notes": []}))
    assert "Serial.print(s1_a.acceleration.x);" in code


def test_low_power_code_explains_rules_run_once_per_wake():
    cpp, _ = firmware.skeleton(engine.build(plan("bme280", power="lipo", low_power=True)))
    assert "every rule below runs once per wake" in cpp


def test_network_printer_without_slicer_says_what_to_do():
    from forge import machines
    with pytest.raises(machines.MachineError, match="Open in my slicer"):
        machines.send_to_printer("lamp", b"3mf", {"kind": "octoprint", "url": "http://x", "slicer": ""})


def test_uploads_over_the_limit_are_refused(monkeypatch):
    from fastapi.testclient import TestClient
    from forge import web
    monkeypatch.setattr(web, "MAX_UPLOAD", 1000)
    c = TestClient(web.app)
    assert c.post("/api/vbuild", content=b"x" * 2000).status_code == 413


def test_a_full_queue_answers_busy(monkeypatch):
    from fastapi.testclient import TestClient
    from forge import web
    monkeypatch.setattr(web, "MAX_QUEUED", 0)
    monkeypatch.setattr(web, "MAX_JOBS", 1)
    monkeypatch.setitem(web.jobs, "held", {"state": "running", "log": [], "started": 0})
    r = TestClient(web.app).post("/api/build", json={"idea": "a lamp", "provider": "offline"})
    assert r.status_code == 429


def test_status_and_manifest_carry_the_version():
    from fastapi.testclient import TestClient
    from forge import __version__, vbuild, web
    assert TestClient(web.app).get("/api/status").json()["version"] == __version__
    man, _ = vbuild.read(pipeline.bundle(offline("a button")))
    assert man["generator"] == f"Forge {__version__}"


def test_website_build_has_the_viewer_and_playable_samples(tmp_path, monkeypatch):
    import importlib.util
    from forge import vbuild
    spec = importlib.util.spec_from_file_location("build_site", "scripts/build_site.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "OUT", tmp_path / "dist")
    mod.main()
    out = tmp_path / "dist"
    assert (out / "viewer.html").exists() and (out / "static/viewer.js").exists()
    assert (out / "static/vendor/three.module.min.js").exists()
    assert not (out / "static/index.html").exists()        # the app page is not part of the site
    samples = sorted((out / "samples").glob("*.vbuild"))
    assert len(samples) == 3
    for s in samples:
        man, files = vbuild.read(s.read_bytes())
        assert man["model"] in files and man["machines"]["board"]["file"] in files
