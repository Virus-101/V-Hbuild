"""Idea -> plan. The model chooses the parts; the engine checks them.

The model is a local one served by Ollama by default (see llm.py), Claude optionally. Either way
its answer is held to a schema whose part and board fields are enums of
catalog ids, so it cannot name a part that is not stocked. When the engine
rejects a plan (an I2C clash nothing can strap around, too much current, too
many pins) the objections go back to the model for another go, a bounded
number of times.

Without any model the offline planner below does a keyword match. It is
crude, but the whole pipeline - wiring, BOM, firmware, enclosure, the .vbuild
file - runs and can be tested with no model at all.
"""
import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from .catalog import BOARDS, PARTS, POWER_PARTS, planner_view
from .llm import ProviderError

MAX_REPLANS = 2
PlannerError = ProviderError

PartId = Literal[tuple(p for p in PARTS if p not in POWER_PARTS)]  # type: ignore[valid-type]
BoardId = Literal[tuple(BOARDS)]                                    # type: ignore[valid-type]


class PartChoice(BaseModel):
    part_id: PartId
    role: str = Field(description="What this part does in this device, one short phrase.")


class Plan(BaseModel):
    name: str = Field(description="A short product name, 1-4 words.")
    summary: str = Field(description="Two sentences: what it does and for whom.")
    board_id: BoardId
    power: Literal["usb", "usb_adapter", "lipo"]
    low_power: bool = Field(description="True if the device can deep-sleep between readings.")
    parts: list[PartChoice]
    behavior: list[str] = Field(description="What the firmware must do, as short imperative steps.")
    assumptions: list[str] = Field(description="Choices you made that the user did not specify.")
    out_of_scope: list[str] = Field(description="Parts of the idea this catalog cannot build. Empty if none.")


SYSTEM = """You are the planning stage of a hardware builder. A person describes a device \
idea; you turn it into a plan made only of catalog parts. A deterministic engine then \
assigns pins, I2C addresses, power rails and passives and checks the electrical rules, \
so do not describe wiring or voltages - choose parts.

Rules:
- Choose only parts the idea needs. Do not add a buzzer, servo, button, LED ring or \
relay the person did not ask for. One sensor per quantity: never two humidity sensors.
- Choose the cheapest board that fits; the ESP32-S3 only when many parts need pins.
- power: "usb" for desk devices, "lipo" when the idea implies portable or placed \
somewhere without an outlet, "usb_adapter" for servos, pumps or LED rings.
- low_power: true only if the device just samples and reports, with no display, \
motion sensor, button or actuator.
- out_of_scope: only things the person asked for that no catalog part provides. \
Usually this is an empty list.
- behavior: concrete steps the firmware performs, with numbers.

Example 1
Idea: a thermometer for my desk that shows the temperature
Plan: board esp32-c3-devkitm-1, power usb, low_power false,
  parts: sht31 (reads temperature and humidity), ssd1306 (shows the readings),
  behavior: ["Every 2 seconds read temperature and humidity", "Show both on the screen"],
  out_of_scope: []

Example 2
Idea: water my fern when the soil gets dry, and send me the moisture on my phone
Plan: board esp32-c3-devkitm-1, power usb_adapter, low_power false,
  parts: soil-capacitive (measures soil moisture), pump-5v (waters the fern), relay-1ch (switches the pump),
  behavior: ["Every 10 minutes read soil moisture", "If below 35% run the pump for 3 seconds",
             "Print the moisture over serial"],
  out_of_scope: ["Phone notifications need a Wi-Fi service; the board has Wi-Fi but no app is generated"]

Catalog:
"""

CATALOG_JSON = json.dumps(planner_view(), indent=1)


def llm_plan(idea: str, provider, objections: list[str] | None = None,
             previous: dict | None = None) -> dict:
    """One planning call. `objections` are the engine's errors on `previous`."""
    content = f"Device idea:\n{idea.strip()}"
    if objections:
        content += ("\n\nYour previous plan was:\n" + json.dumps(previous, indent=1)
                    + "\n\nThe engine rejected it:\n- " + "\n- ".join(objections)
                    + "\n\nReturn a corrected plan.")
    plan = provider.structured(SYSTEM + CATALOG_JSON, content, Plan).model_dump()
    return review(idea, plan)


# --- review -------------------------------------------------------------

# Parts that cost money, power or attention; a plan needs the idea to ask for them.
MUST_BE_ASKED = ("sg90", "buzzer", "ws2812b-ring12", "pump-5v", "relay-1ch", "button")
# (kept, dropped): the second adds nothing when the first is in the plan
REDUNDANT = (("bme280", "sht31"), ("vl53l0x", "hcsr04"))
COUNT_WORDS = re.compile(r"\b(two|three|four|pair|both|several|multiple|\d+)\b")


def _asked(part_id: str, text: str) -> bool:
    return any(re.search(rf"\b(?:{pattern})", text)
               for pattern, pid, _ in KEYWORDS if pid == part_id)


def review(idea: str, plan: dict) -> dict:
    """Hold a model's plan to the idea. Small models pad plans with parts nobody
    asked for; the engine keeps those electrically correct, but not wanted.
    Every change is recorded in the plan's assumptions."""
    text = idea.lower()
    parts, notes, seen, removed = [], [], set(), set()
    ids = {p["part_id"] for p in plan.get("parts", [])}
    for p in plan.get("parts", []):
        pid = p["part_id"]
        if pid in seen and not COUNT_WORDS.search(text):
            notes.append(f"Removed a second {PARTS[pid].name}: the idea asks for one.")
            continue
        if pid in MUST_BE_ASKED and not _asked(pid, text) and not (pid == "relay-1ch" and "pump-5v" in ids):
            notes.append(f"Removed {PARTS[pid].name}: the idea does not ask for it.")
            removed.add(pid)
            continue
        if any(pid == drop and keep in ids for keep, drop in REDUNDANT):
            notes.append(f"Removed {PARTS[pid].name}: another part already measures that.")
            continue
        seen.add(pid)
        parts.append(p)
    if not parts:       # the review should narrow a plan, never empty it
        return plan
    busy = any(PARTS[p["part_id"]].category in ("display", "actuator", "output", "input")
               or p["part_id"] == "pir-hcsr501" for p in parts)
    low_power = bool(plan.get("low_power")) and not busy
    if plan.get("low_power") and not low_power:
        notes.append("Turned low-power mode off: a screen, motion sensor or actuator needs the board awake.")
    # Instructions about removed parts would make the firmware writer invent them.
    gone = [pattern for pattern, pid, _ in KEYWORDS if pid in removed]

    def mentions_removed(line: str) -> bool:
        return any(re.search(rf"\b(?:{g})", line.lower()) for g in gone)
    behavior = [b for b in plan.get("behavior", []) if not mentions_removed(b)]
    kept_assumptions = [a for a in plan.get("assumptions", []) if not mentions_removed(a)]
    words = {w for w in re.findall(r"[a-z]{4,}", text)}
    scope = [x for x in plan.get("out_of_scope", [])
             if words & set(re.findall(r"[a-z]{4,}", x.lower()))]
    return {**plan, "parts": parts, "low_power": low_power, "out_of_scope": scope,
            "behavior": behavior, "assumptions": kept_assumptions + notes}


# --- offline ------------------------------------------------------------

KEYWORDS: list[tuple[str, str, str]] = [
    # (regex, part_id, role)
    (r"soil|plant|garden", "soil-capacitive", "measures soil moisture"),
    (r"water(s|ing)?\b|pump|irrigat", "pump-5v", "waters the plant"),
    (r"humid|weather|climate|air quality|pressure|barometer", "bme280", "reads temperature, humidity and pressure"),
    (r"pool|aquarium|liquid|probe|fish ?tank|brew", "ds18b20", "measures liquid temperature"),
    (r"temperat|thermo", "sht31", "reads air temperature and humidity"),
    (r"light level|lux|brightness|daylight|sunlight", "bh1750", "measures ambient light"),
    (r"motion|presence|intruder|someone|people|occupan|pir\b", "pir-hcsr501", "detects people moving"),
    (r"distance|parking|level of|how full|tank level|range", "hcsr04", "measures distance"),
    (r"precise distance|tof|gesture", "vl53l0x", "measures short distances precisely"),
    (r"tilt|shake|orientation|accelero|gyro|fall|step", "mpu6050", "senses motion and tilt"),
    (r"display|screen|show|oled|readout|dashboard", "ssd1306", "shows readings"),
    (r"leds?\b|lamp|glow|colou?r|light up|notify|status light|ring", "ws2812b-ring12", "shows status with colour"),
    (r"alarm|beep|buzz|alert|sound", "buzzer", "sounds an alert"),
    (r"servo|feeder|lock|latch|flag|point|wave|rotate", "sg90", "moves the mechanism"),
    (r"relay|switch (a|the|on)|mains|heater|fan|lamp on", "relay-1ch", "switches the load"),
    (r"button|press(es|ed)?\b|push|click|snooze|reset", "button", "user input"),
]


def offline_plan(idea: str) -> dict:
    text = idea.lower()
    parts, seen = [], set()
    for pattern, pid, role in KEYWORDS:
        if pid not in seen and re.search(rf"\b(?:{pattern})", text):
            seen.add(pid)
            parts.append({"part_id": pid, "role": role})
    # A distance idea that already has the precise sensor needs only one.
    if "vl53l0x" in seen and "hcsr04" in seen:
        parts = [p for p in parts if p["part_id"] != "hcsr04"]
    if "bme280" in seen and "sht31" in seen:
        parts = [p for p in parts if p["part_id"] != "sht31"]
    if not parts:
        parts = [{"part_id": "sht31", "role": "reads temperature and humidity"},
                 {"part_id": "ssd1306", "role": "shows readings"}]

    portable = re.search(r"portable|battery|wearable|outdoor|garden|remote|wireless|pocket", text)
    heavy = any(p["part_id"] in ("sg90", "pump-5v", "ws2812b-ring12") for p in parts)
    power = "lipo" if portable and not heavy else ("usb_adapter" if heavy else "usb")
    low_power = power == "lipo" and not any(
        PARTS[p["part_id"]].category in ("display", "actuator", "output")
        or p["part_id"] in ("pir-hcsr501", "button") for p in parts)

    words = re.findall(r"[a-zA-Z]+", idea)
    stop = {"a", "an", "the", "i", "want", "to", "that", "which", "build", "make", "for",
            "my", "device", "thing", "something", "with", "and", "of", "me", "when", "it"}
    name = " ".join(w.capitalize() for w in words if w.lower() not in stop)[:32].strip() or "Device"
    behavior = [f"{PARTS[p['part_id']].name}: {p['role']}." for p in parts]
    return {
        "name": " ".join(name.split()[:3]),
        "summary": idea.strip()[:300],
        "board_id": "esp32-c3-devkitm-1",
        "power": power,
        "low_power": low_power,
        "parts": parts,
        "behavior": behavior,
        "assumptions": ["Planned offline by keyword match - start a model in Ollama for a real plan."],
        "out_of_scope": [],
    }
