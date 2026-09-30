"""Firmware: a PlatformIO project for the design.

The skeleton is generated from per-part snippets, so it compiles and reads
every sensor without any model involved. The model then describes the
device's behaviour as rules - "every 600 s, if s1_pct < 35, run M2 for 3 s" -
against a schema whose variables and actions are exactly what this device
has, and the C++ for those rules is generated here. The model never writes
code, so it cannot move a pin or break the build.
"""
import re

from typing import Literal

from pydantic import BaseModel, Field

from .engine import Design, Instance

SERVO_LIB = {"esp32": ("#include <ESP32Servo.h>", ("madhephaestus/ESP32Servo",)),
             "rp2040": ("#include <Servo.h>", ())}


def _v(inst: Instance) -> str:
    return inst.ref.lower()


def _snippet(inst: Instance, family: str) -> dict:
    """includes, globals, setup, read (lines), readings [(label, expr)], helpers."""
    v, R, p, pid = _v(inst), inst.ref, inst.pins, inst.part.id
    addr = f"0x{inst.address:02X}" if inst.address is not None else ""
    s = {"includes": [], "globals": [], "setup": [], "read": [], "readings": [],
         "helpers": [], "lib_deps": list(inst.part.lib_deps)}
    pin_consts = [f"const int {R}_{sig} = {pin};" for sig, pin in p.items()
                  if inst.part.interface != "i2c"]
    s["globals"] += pin_consts

    if pid == "bme280":
        s["includes"].append("#include <Adafruit_BME280.h>")
        s["globals"].append(f"Adafruit_BME280 {v};")
        s["setup"].append(f'if (!{v}.begin({addr}, &Wire)) Serial.println("{R}: BME280 not found");')
        s["read"] += [f"float {v}_temp = {v}.readTemperature();",
                      f"float {v}_hum = {v}.readHumidity();",
                      f"float {v}_hpa = {v}.readPressure() / 100.0F;"]
        s["readings"] += [("temp_c", f"{v}_temp"), ("humidity", f"{v}_hum"), ("hpa", f"{v}_hpa")]
    elif pid == "sht31":
        s["includes"].append("#include <Adafruit_SHT31.h>")
        s["globals"].append(f"Adafruit_SHT31 {v} = Adafruit_SHT31();")
        s["setup"].append(f'if (!{v}.begin({addr})) Serial.println("{R}: SHT31 not found");')
        s["read"] += [f"float {v}_temp = {v}.readTemperature();",
                      f"float {v}_hum = {v}.readHumidity();"]
        s["readings"] += [("temp_c", f"{v}_temp"), ("humidity", f"{v}_hum")]
    elif pid == "bh1750":
        s["includes"].append("#include <BH1750.h>")
        s["globals"].append(f"BH1750 {v};")
        s["setup"].append(f"{v}.begin(BH1750::CONTINUOUS_HIGH_RES_MODE, {addr}, &Wire);")
        s["read"].append(f"float {v}_lux = {v}.readLightLevel();")
        s["readings"].append(("lux", f"{v}_lux"))
    elif pid == "vl53l0x":
        s["includes"].append("#include <VL53L0X.h>")
        s["globals"].append(f"VL53L0X {v};")
        s["setup"] += [f"{v}.setTimeout(500);",
                       f'if (!{v}.init()) Serial.println("{R}: VL53L0X not found");',
                       f"{v}.startContinuous();"]
        s["read"].append(f"int {v}_mm = {v}.readRangeContinuousMillimeters();")
        s["readings"].append(("distance_mm", f"{v}_mm"))
    elif pid == "mpu6050":
        s["includes"].append("#include <Adafruit_MPU6050.h>")
        s["globals"].append(f"Adafruit_MPU6050 {v};")
        s["setup"].append(f'if (!{v}.begin({addr}, &Wire)) Serial.println("{R}: MPU6050 not found");')
        s["read"] += [f"sensors_event_t {v}_a, {v}_g, {v}_t;",
                      f"{v}.getEvent(&{v}_a, &{v}_g, &{v}_t);"]
        s["readings"] += [("ax", f"{v}_a.acceleration.x"), ("ay", f"{v}_a.acceleration.y"),
                          ("az", f"{v}_a.acceleration.z")]
    elif pid == "ssd1306":
        s["includes"] += ["#include <Adafruit_GFX.h>", "#include <Adafruit_SSD1306.h>"]
        s["globals"].append(f"Adafruit_SSD1306 {v}(128, 64, &Wire, -1);")
        s["setup"] += [f'if (!{v}.begin(SSD1306_SWITCHCAPVCC, {addr})) Serial.println("{R}: SSD1306 not found");',
                       f"{v}.clearDisplay();", f"{v}.display();"]
    elif pid == "ds18b20":
        s["includes"] += ["#include <OneWire.h>", "#include <DallasTemperature.h>"]
        s["globals"] += [f"OneWire {v}_bus({R}_DQ);", f"DallasTemperature {v}(&{v}_bus);"]
        s["setup"].append(f"{v}.begin();")
        s["read"] += [f"{v}.requestTemperatures();",
                      f"float {v}_temp = {v}.getTempCByIndex(0);"]
        s["readings"].append(("probe_c", f"{v}_temp"))
    elif pid == "soil-capacitive":
        s["globals"] += [f"// Calibrate: raw reading in dry air, then in a glass of water.",
                         f"const int {R}_DRY = 3000;", f"const int {R}_WET = 1300;"]
        s["read"] += [f"int {v}_raw = analogRead({R}_AOUT);",
                      f"int {v}_pct = constrain(map({v}_raw, {R}_DRY, {R}_WET, 0, 100), 0, 100);"]
        s["readings"].append(("soil_pct", f"{v}_pct"))
    elif pid == "pir-hcsr501":
        s["setup"].append(f"pinMode({R}_OUT, INPUT);")
        s["read"].append(f"bool {v}_motion = digitalRead({R}_OUT) == HIGH;")
        s["readings"].append(("motion", f"{v}_motion"))
    elif pid == "hcsr04":
        s["setup"] += [f"pinMode({R}_TRIG, OUTPUT);", f"pinMode({R}_ECHO, INPUT);"]
        s["helpers"].append(
            f"float {v}_distance_cm() {{\n"
            f"  digitalWrite({R}_TRIG, LOW); delayMicroseconds(2);\n"
            f"  digitalWrite({R}_TRIG, HIGH); delayMicroseconds(10);\n"
            f"  digitalWrite({R}_TRIG, LOW);\n"
            f"  long us = pulseIn({R}_ECHO, HIGH, 30000);\n"
            f"  return us == 0 ? -1 : us / 58.0;\n}}")
        s["read"].append(f"float {v}_cm = {v}_distance_cm();")
        s["readings"].append(("distance_cm", f"{v}_cm"))
    elif pid == "button":
        s["setup"].append(f"pinMode({R}_OUT, INPUT_PULLUP);")
        s["read"].append(f"bool {v}_pressed = digitalRead({R}_OUT) == LOW;")
        s["readings"].append(("pressed", f"{v}_pressed"))
    elif pid == "ws2812b-ring12":
        s["includes"].append("#include <Adafruit_NeoPixel.h>")
        s["globals"].append(f"Adafruit_NeoPixel {v}(12, {R}_DIN, NEO_GRB + NEO_KHZ800);")
        s["setup"] += [f"{v}.begin();",
                       f"{v}.setBrightness(64);  // 25% - the power budget assumes this cap",
                       f"{v}.show();"]
        s["helpers"].append(
            f"void {v}_fill(uint8_t r, uint8_t g, uint8_t b) {{\n"
            f"  for (int i = 0; i < {v}.numPixels(); i++) {v}.setPixelColor(i, {v}.Color(r, g, b));\n"
            f"  {v}.show();\n}}")
    elif pid == "buzzer":
        s["setup"] += [f"pinMode({R}_IN, OUTPUT);", f"digitalWrite({R}_IN, LOW);"]
        s["helpers"].append(
            f"void {v}_beep(int ms) {{\n"
            f"  digitalWrite({R}_IN, HIGH); delay(ms); digitalWrite({R}_IN, LOW);\n}}")
    elif pid == "sg90":
        include, deps = SERVO_LIB[family]
        s["includes"].append(include)
        s["lib_deps"] += list(deps)
        s["globals"].append(f"Servo {v};")
        s["setup"] += [f"{v}.attach({R}_SIG);", f"{v}.write(90);"]
    elif pid == "relay-1ch":
        s["globals"] += ["// Most relay modules trigger on HIGH; set to LOW if yours is active-low.",
                         f"const int {R}_ON = HIGH;"]
        s["setup"] += [f"pinMode({R}_IN, OUTPUT);", f"digitalWrite({R}_IN, !{R}_ON);"]
        s["helpers"].append(
            f"void {v}_set(bool on) {{ digitalWrite({R}_IN, on ? {R}_ON : !{R}_ON); }}")
    return s


def skeleton(d: Design) -> tuple[str, list[str]]:
    """(main.cpp, lib_deps) that reads every sensor and prints one JSON line per cycle."""
    fam = d.board.family
    parts = [(i, _snippet(i, fam)) for i in d.instances if i.rail]
    includes, lib_deps = [], []
    for _, s in parts:
        for x in s["includes"]:
            if x not in includes:
                includes.append(x)
        for x in s["lib_deps"]:
            if x not in lib_deps:
                lib_deps.append(x)
    has_i2c = any(i.part.interface == "i2c" for i, _ in parts)
    display = next((i for i in d.instances if i.part.id == "ssd1306"), None)
    readings = [(f"{i.ref.lower()}_{label}", expr) for i, s in parts for label, expr in s["readings"]]
    sleepy = d.low_power and fam == "esp32"

    out = [f"// {d.name} - generated by V-Hbuild for the {d.board.name}.",
           "// Pin numbers below come from the wiring table; change both together.",
           "#include <Arduino.h>"]
    if has_i2c:
        out.append("#include <Wire.h>")
    out += includes + [""]
    for i, s in parts:
        if s["globals"]:
            out.append(f"// {i.ref}: {i.part.name} - {i.role}")
            out += s["globals"]
    out.append("")
    out.append(CONSTANTS_MARK)
    out.append("const unsigned long INTERVAL_MS = 2000;")
    if sleepy:
        out.append("// Low power: the board wakes, runs loop() once, and sleeps again. Memory")
        out.append("// is cleared while it sleeps, so every rule below runs once per wake.")
        out.append("const uint64_t SLEEP_SECONDS = 600;")
    out.append("")
    for _, s in parts:
        for h in s["helpers"]:
            out += [h, ""]

    out += ["void setup() {", "  Serial.begin(115200);", "  delay(200);"]
    if has_i2c:
        sda, scl = d.board.i2c
        if fam == "rp2040":
            out += [f"  Wire.setSDA({sda});", f"  Wire.setSCL({scl});", "  Wire.begin();"]
        else:
            out.append(f"  Wire.begin({sda}, {scl});")
    if any(i.part.interface == "analog" for i, _ in parts):
        out.append("  analogReadResolution(12);")
    for _, s in parts:
        out += [f"  {line}" for line in s["setup"]]
    out += ["}", "", "void loop() {"]
    for _, s in parts:
        out += [f"  {line}" for line in s["read"]]
    if readings:
        out.append('  Serial.print("{");')
        for n, (label, expr) in enumerate(readings):
            sep = "" if n == 0 else ","
            out.append(f'  Serial.print("{sep}\\"{label}\\":"); Serial.print({expr});')
        out.append('  Serial.println("}");')
    if display and readings:
        v = display.ref.lower()
        out += [f"  {v}.clearDisplay();", f"  {v}.setTextSize(1);",
                f"  {v}.setTextColor(SSD1306_WHITE);", f"  {v}.setCursor(0, 0);"]
        for label, expr in readings[:7]:
            out.append(f'  {v}.print("{label.split("_", 1)[1]}: "); {v}.println({expr});')
        out.append(f"  {v}.display();")
    out.append("")
    out.append("  " + BEGIN_MARK)
    out.append("  // Behaviour to implement:")
    out += [f"  //  - {b}" for b in d.behavior] or ["  //  (none given)"]
    out.append("  " + END_MARK)
    out.append("")
    if sleepy:
        out += ["  Serial.flush();",
                "  esp_sleep_enable_timer_wakeup(SLEEP_SECONDS * 1000000ULL);",
                "  esp_deep_sleep_start();"]
    else:
        out.append("  delay(INTERVAL_MS);")
    out.append("}")
    return "\n".join(out) + "\n", lib_deps


def platformio_ini(d: Design, lib_deps: list[str]) -> str:
    env = d.board.pio_env
    lines = ["; Generated by V-Hbuild. Build and flash with: pio run -t upload",
             f"[env:{env['board']}]"]
    lines += [f"{k} = {v}" for k, v in env.items()]
    lines.append("monitor_speed = 115200")
    if lib_deps:
        lines.append("lib_deps =")
        lines += [f"    {dep}" for dep in lib_deps]
    return "\n".join(lines) + "\n"


CONSTANTS_MARK = "// VHBUILD:CONSTANTS"
BEGIN_MARK = "// VHBUILD:BEHAVIOUR-BEGIN"
END_MARK = "// VHBUILD:BEHAVIOUR-END"


COLORS = {"off": (0, 0, 0), "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
          "white": (255, 255, 255), "yellow": (255, 180, 0), "orange": (255, 90, 0), "purple": (160, 0, 255)}


def menu(d: Design) -> tuple[dict, dict]:
    """What this device can sense and do: {variable: (expr, is_bool, meaning)},
    {action: (template, meaning)}. The behaviour schema is built from these."""
    variables, actions = {}, {}
    for inst in d.instances:
        if not inst.rail:
            continue
        s, R, v = _snippet(inst, d.board.family), inst.ref, inst.ref.lower()
        for label, expr in s["readings"]:
            is_bool = label in ("motion", "pressed")
            variables[expr] = (expr, is_bool, f"{label.replace('_', ' ')} from {R} ({inst.part.name})")
        pid = inst.part.id
        if pid == "relay-1ch":
            what = "the pump" if d.has("pump-5v") else "the load"
            actions[f"{R}.switch_on"] = (f"{v}_set(true);", f"switch {what} on")
            actions[f"{R}.switch_off"] = (f"{v}_set(false);", f"switch {what} off")
            actions[f"{R}.switch_on_for"] = (f"{v}_set(true); delay({{ms}}); {v}_set(false);",
                                             f"run {what} for `amount` seconds")
        elif pid == "buzzer":
            actions[f"{R}.beep"] = (f"{v}_beep({{ms}});", "beep for `amount` seconds")
        elif pid == "ws2812b-ring12":
            actions[f"{R}.color"] = (f"{v}_fill({{rgb}});", "set the lights to colour `text` "
                                                          f"({', '.join(COLORS)})")
        elif pid == "sg90":
            actions[f"{R}.move"] = (f"{v}.write({{angle}});", "turn the servo to `amount` degrees (0-180)")
        elif pid == "ssd1306":
            actions[f"{R}.show"] = (f"{{say:{v}}} {v}.display();", "show the message `text` on the screen")
    actions["serial.print"] = ("{say:Serial}", "print the message `text` to the USB serial log")
    return variables, actions


def behaviour_model(d: Design) -> type[BaseModel]:
    """A schema whose variables and actions are enums of what this device has."""
    variables, actions = menu(d)
    Var = Literal[("always", *variables)]            # type: ignore[valid-type]
    Do = Literal[tuple(actions)]                      # type: ignore[valid-type]

    class Action(BaseModel):
        do: Do
        amount: float = Field(0, description="seconds, or degrees for a servo")
        text: str = Field("", description="message text, or a colour name")

    class Rule(BaseModel):
        every_seconds: int = Field(description="how often to check; 0 = every loop (for buttons and motion)")
        variable: Var
        compare: Literal["<", ">", "is true", "is false", "none"]
        threshold: float = 0
        then: list[Action]

    class Behaviour(BaseModel):
        rules: list[Rule]
        notes: list[str] = Field(description="what the builder should tune; may be empty")

    return Behaviour


FIRMWARE_SYSTEM = """You turn a device's behaviour into rules. The device reads its \
sensors every 2 seconds into the variables listed; each rule checks one variable and \
runs actions.

A rule: every_seconds (how often; 0 = every loop, for buttons and motion), variable \
("always" for no condition), compare ("<", ">", "is true", "is false", "none" with \
"always"), threshold (a number), then (actions from the list, in order).

Example - "every 10 minutes, if soil moisture is below 35% run the pump for 3 seconds":
{"every_seconds": 600, "variable": "s1_pct", "compare": "<", "threshold": 35,
 "then": [{"do": "M2.switch_on_for", "amount": 3, "text": ""}]}

A message `text` can include a variable's current value in braces:
{"do": "serial.print", "text": "Moisture: {s1_pct}%"}"""


def _c_string(text: str) -> str:
    # The display font is ASCII only; keep serial output plain too.
    text = text.replace("°", " deg")
    safe = "".join(c for c in text if 32 <= ord(c) < 127)[:40]
    return '"' + safe.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _say(obj: str, text: str, variables: dict) -> str:
    """print/println statements for a message, with {variable} replaced by its value."""
    text = text.strip()
    if text in variables:                       # a bare variable name means its value
        text = "{" + text + "}"
    parts = re.split(r"\{([\w.]+)\}", text)[:13]     # dots: s6_a.acceleration.x
    out = []
    for i, piece in enumerate(parts):
        if i % 2:                               # odd pieces are what was inside braces
            if piece in variables:
                out.append(f"{obj}.print({variables[piece][0]});")
            else:
                out.append(f"{obj}.print({_c_string('{' + piece + '}')});")
        elif piece:
            out.append(f"{obj}.print({_c_string(piece)});")
    out.append(f"{obj}.println();")
    return " ".join(out)


def rules_to_code(d: Design, b) -> tuple[str, str]:
    """(constants, code) for the skeleton, generated from validated rules - always valid C++."""
    variables, actions = menu(d)
    consts, code = [], []
    for n, r in enumerate(b.rules[:12]):
        steps = []
        for a in r.then[:6]:
            template = actions[a.do][0]
            seconds = min(max(a.amount, 0), 60)
            color = COLORS.get(a.text.strip().lower(), COLORS["white"])
            if "{say:" in template:
                obj = template.split("{say:", 1)[1].split("}", 1)[0]
                template = template.replace("{say:" + obj + "}", _say(obj, a.text, variables).replace("{", "{{").replace("}", "}}"))
            steps.append(template.format(ms=int(seconds * 1000), angle=int(min(max(a.amount, 0), 180)),
                                         rgb=", ".join(map(str, color))))
        if not steps:
            continue
        cond = "true"
        if r.variable != "always":
            expr, is_bool, _ = variables[r.variable]
            if is_bool:
                cond = f"!{expr}" if r.compare == "is false" else expr
            elif r.compare in ("<", ">"):
                cond = f"{expr} {r.compare} {r.threshold:g}"
            # a number with no comparison is no condition at all
        body = " ".join(steps)
        if r.every_seconds > 0:
            every = max(2, min(r.every_seconds, 86400))
            consts.append(f"const unsigned long RULE{n + 1}_EVERY_MS = {every}000UL;")
            consts.append(f"unsigned long rule{n + 1}Last = 0;")
            code.append(f"if (rule{n + 1}Last == 0 || millis() - rule{n + 1}Last >= RULE{n + 1}_EVERY_MS) {{")
            code.append(f"  rule{n + 1}Last = millis();")
            code.append(f"  if ({cond}) {{ {body} }}")
            code.append("}")
        else:
            code.append(f"if ({cond}) {{ {body} }}")
    return "\n".join(consts), "\n".join(code)


FORBIDDEN = re.compile(r"#include|\bvoid\s+(setup|loop)\s*\(|\bpinMode\s*\(|"
                       r"\b[A-Z]+\d+_[A-Z]+\s*=[^=]|esp_deep_sleep_start")


def api(d: Design) -> str:
    variables, actions = menu(d)
    lines = ["Variables (use in a rule, or in a message as {name}):"]
    lines += [f"- {k}: {m}" + (" (true/false)" if b else " (a number)") for k, (_, b, m) in variables.items()]
    lines += ["Actions:"] + [f"- {k}: {m}" for k, (_, m) in actions.items()]
    return "\n".join(lines)


def splice(base: str, constants: str, code_text: str) -> str | None:
    """The skeleton with the behaviour inserted, or None if the behaviour breaks the rules."""
    if FORBIDDEN.search(constants) or FORBIDDEN.search(code_text):
        return None
    if CONSTANTS_MARK not in base or BEGIN_MARK not in base:
        return None
    head, rest = base.split("  " + BEGIN_MARK, 1)
    _, tail = rest.split("  " + END_MARK, 1)
    code = "\n".join("  " + line if line.strip() else "" for line in code_text.strip().splitlines())
    body = f"{head}  // Behaviour, from the planner's rules:\n{code}\n{tail.lstrip(chr(10))}"
    consts = constants.strip()
    return body.replace(CONSTANTS_MARK, f"// Behaviour settings - tune these:\n{consts}\n" if consts else "")


def finish(base: str) -> str:
    """Drop the markers from a skeleton that ships as it is."""
    return (base.replace(CONSTANTS_MARK + "\n", "")
                .replace("  " + BEGIN_MARK + "\n", "").replace("  " + END_MARK + "\n", ""))


def llm_firmware(d: Design, base: str, provider) -> tuple[str, list[str], list[str]]:
    """Behaviour as rules, turned into C++ here. Falls back to the skeleton on any doubt.

    Returns (main.cpp, V-Hbuild's own notes, the model's notes). The model's notes are
    its words, unchecked, and are kept apart so they are never presented as V-Hbuild's."""
    from .llm import ProviderError
    try:
        b = provider.structured(
            FIRMWARE_SYSTEM,
            f"Device: {d.name} - {d.summary}\n\nBehaviour:\n- "
            + "\n- ".join(d.behavior or ["Report readings over serial."])
            + f"\n\n{api(d)}",
            behaviour_model(d), max_tokens=2000)
    except ProviderError as e:
        return finish(base), [f"Behaviour could not be generated ({e}); the skeleton reads every part."], []
    constants, code = rules_to_code(d, b)
    cpp = splice(base, constants, code) if code else None
    if cpp is None:
        return finish(base), ["No usable behaviour rules came back; the skeleton reads every part."], []
    return cpp, [], [n for n in b.notes if n.strip()][:8]
