"""Turn a plan (which parts, which board, how powered) into a buildable design.

Everything here is deterministic. The planner chooses; this module assigns
pins and I2C addresses, sizes the power rails, adds the resistors and
capacitors the parts need, and records every rule a design breaks. Nothing
the planner says about electricity is trusted - it is not asked to say any.
"""
from dataclasses import dataclass, field

from .catalog import (ADAPTER_BUDGET_MA, BOARDS, PARTS, PASSIVES, POWER_PARTS,
                      USB_BUDGET_MA, Board, Part)

# The firmware caps WS2812 brightness at this fraction, so the ring is
# budgeted at that share of its all-white peak rather than all of it.
LED_BRIGHTNESS_CAP = 0.25
BOOST_MAX_MA = 1000       # an MT3608 from one LiPo cell, honestly
BOOST_EFFICIENCY = 0.85
BATTERY_MAH = 2000
BATTERY_USABLE = 0.8
LOW_POWER_DUTY = 0.02     # awake 2% of the time when the plan says low_power

SIGNALS = {
    "i2c": ("SDA", "SCL"),
    "digital_in": ("OUT",),
    "digital_out": ("IN",),
    "pwm": ("SIG",),
    "analog": ("AOUT",),
    "onewire": ("DQ",),
    "ultrasonic": ("TRIG", "ECHO"),
    "neopixel": ("DIN",),
    "power": (),
}

REF_PREFIX = {"sensor": "S", "input": "SW", "display": "DS", "output": "L",
              "actuator": "M", "power": "PS"}


@dataclass
class Issue:
    severity: str            # error | warning | info
    message: str
    replan: bool = False     # True when a different choice of parts fixes it

    def as_dict(self) -> dict:
        return {"severity": self.severity, "message": self.message,
                "replan": self.replan}


@dataclass
class Instance:
    ref: str
    part: Part
    role: str
    rail: str = ""                       # "3V3" | "5V" | ""
    pins: dict = field(default_factory=dict)   # signal -> GPIO number
    address: int | None = None
    auto: bool = False                   # added by the engine, not the planner

    def as_dict(self) -> dict:
        return {"ref": self.ref, "part_id": self.part.id, "name": self.part.name,
                "role": self.role, "rail": self.rail, "pins": self.pins,
                "address": f"0x{self.address:02X}" if self.address is not None else None,
                "auto": self.auto}


@dataclass
class Design:
    name: str
    summary: str
    board: Board
    power: str
    low_power: bool
    behavior: list[str]
    instances: list[Instance] = field(default_factory=list)
    passives: dict = field(default_factory=dict)   # id -> (qty, reason)
    wires: list[tuple[str, str, str]] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    budget: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def parts(self, part_id: str) -> list[Instance]:
        return [i for i in self.instances if i.part.id == part_id]

    def has(self, part_id: str) -> bool:
        return any(i.part.id == part_id for i in self.instances)

    def as_dict(self) -> dict:
        return {
            "name": self.name, "summary": self.summary,
            "board": {"id": self.board.id, "name": self.board.name,
                      "i2c": {"sda": self.board.i2c[0], "scl": self.board.i2c[1]}},
            "power": self.power, "low_power": self.low_power,
            "behavior": self.behavior,
            "instances": [i.as_dict() for i in self.instances],
            "passives": [{"id": k, "name": PASSIVES[k][0], "qty": q, "reason": r}
                         for k, (q, r) in self.passives.items()],
            "wires": [{"from": a, "to": b, "note": n} for a, b, n in self.wires],
            "issues": [i.as_dict() for i in self.issues],
            "budget": self.budget, "ok": self.ok,
        }


def build(plan: dict) -> Design:
    """plan: {name, summary, board_id, power, low_power, parts:[{part_id, role}], behavior}."""
    issues: list[Issue] = []
    chosen = []
    for item in plan.get("parts", []):
        part = PARTS.get(item.get("part_id", ""))
        if part is None:
            issues.append(Issue("error", f"Unknown part '{item.get('part_id')}'.", True))
        elif part.id in POWER_PARTS:
            continue    # power parts follow from `power`, the planner does not pick them
        else:
            chosen.append((part, item.get("role") or part.does))

    if any(p.id == "pump-5v" for p, _ in chosen) and not any(p.id == "relay-1ch" for p, _ in chosen):
        chosen.append((PARTS["relay-1ch"], "switches the pump"))
        issues.append(Issue("info", "Added a relay: the pump draws more than a GPIO can switch."))

    board = BOARDS.get(plan.get("board_id", ""))
    if board is None:
        issues.append(Issue("warning", f"Unknown board '{plan.get('board_id')}', using the ESP32-C3."))
        board = BOARDS["esp32-c3-devkitm-1"]

    # Pins first: if they run out, the cheapest board that fits takes over.
    trial = _allocate(board, chosen)
    if trial is None:
        for other in sorted(BOARDS.values(), key=lambda b: b.price):
            if other.id != board.id and (trial := _allocate(other, chosen)) is not None:
                issues.append(Issue("warning",
                    f"{board.name} does not have enough free pins for these parts; "
                    f"switched to {other.name}."))
                board = other
                break
    if trial is None:
        issues.append(Issue("error",
            "No supported board has enough free pins (or ADC inputs) for this many parts. "
            "Drop some parts.", True))
        trial = ([], [])

    power = plan.get("power") if plan.get("power") in ("usb", "usb_adapter", "lipo") else "usb"
    design = Design(name=plan.get("name") or "Untitled device",
                    summary=plan.get("summary") or "",
                    board=board, power=power,
                    low_power=bool(plan.get("low_power")),
                    behavior=list(plan.get("behavior") or []),
                    issues=issues)
    design.instances, addr_issues = trial
    design.issues += addr_issues

    _power(design)
    _passives(design)
    _wire(design)
    return design


def _allocate(board: Board, chosen: list[tuple[Part, str]]):
    """Assign refs, GPIO and I2C addresses. None if the board runs out of pins."""
    sda, scl = board.i2c
    analog = [p for p in board.analog if p not in (sda, scl)]
    digital = [p for p in board.digital if p not in (sda, scl)]
    used: set[int] = set()
    taken_addr: dict[int, str] = {}
    issues: list[Issue] = []
    instances: list[Instance] = []
    counters: dict[str, int] = {}

    # Analog parts first - ADC pins are the scarce ones - then the rest,
    # preferring pins that are not ADC-capable so analog stays available.
    order = sorted(chosen, key=lambda pc: pc[0].interface != "analog")
    for part, role in order:
        prefix = REF_PREFIX.get(part.category, "X")
        counters[prefix] = counters.get(prefix, 0) + 1
        inst = Instance(ref=f"{prefix}{counters[prefix]}", part=part, role=role)

        if part.interface == "i2c":
            inst.pins = {"SDA": sda, "SCL": scl}
            free = [a for a in part.i2c_addr if a not in taken_addr]
            if not free:
                clash = ", ".join(sorted({taken_addr[a] for a in part.i2c_addr}))
                issues.append(Issue("error",
                    f"{part.name} ({inst.ref}) has no free I2C address - every address it "
                    f"supports is used by {clash}. Choose a different part or remove one.", True))
            else:
                inst.address = free[0]
                taken_addr[free[0]] = f"{part.name} ({inst.ref})"
                if free[0] != part.i2c_addr[0]:
                    issues.append(Issue("info",
                        f"{inst.ref} moved to I2C address 0x{free[0]:02X} to avoid a clash: "
                        f"{part.addr_strap}."))
        else:
            for signal in SIGNALS[part.interface]:
                pool = analog if part.interface == "analog" else digital
                pin = next((p for p in pool if p not in used), None)
                if pin is None:
                    return None
                used.add(pin)
                inst.pins[signal] = pin
        instances.append(inst)

    instances.sort(key=lambda i: (i.ref.rstrip("0123456789"), int(i.ref.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))))
    return instances, issues


def _budget_ma(part: Part) -> float:
    if part.interface == "neopixel":
        return part.peak_ma * LED_BRIGHTNESS_CAP
    return part.peak_ma


def _power(d: Design) -> None:
    for inst in d.instances:
        if not inst.part.supply or inst.part.interface == "power":
            continue
        # "Either" parts go on 3V3 so an analog output never exceeds the ADC's range.
        inst.rail = "5V" if inst.part.supply == "5v" else "3V3"

    rail_3v3 = d.board.self_ma + sum(_budget_ma(i.part) for i in d.instances if i.rail == "3V3")
    rail_5v = sum(_budget_ma(i.part) for i in d.instances if i.rail == "5V")
    if d.has("pump-5v"):
        rail_5v += PARTS["pump-5v"].peak_ma * len(d.parts("pump-5v"))
    total = rail_3v3 + rail_5v     # everything comes in at 5 V (or from the boost)

    if rail_3v3 > d.board.reg_ma:
        d.issues.append(Issue("error",
            f"The 3.3 V rail needs ~{rail_3v3:.0f} mA at peak; the board's regulator gives "
            f"{d.board.reg_ma} mA. Move parts to 5 V or drop some.", True))

    if d.power == "usb" and total > USB_BUDGET_MA:
        d.power = "usb_adapter"
        d.issues.append(Issue("warning",
            f"Peak draw is ~{total:.0f} mA, more than a USB port promises ({USB_BUDGET_MA} mA). "
            f"Power it from a 5 V 2 A adapter instead - added to the BOM."))
    if d.power == "usb_adapter" and total > ADAPTER_BUDGET_MA:
        d.issues.append(Issue("error",
            f"Peak draw is ~{total:.0f} mA, more than a 2 A adapter supplies.", True))

    auto = []
    if d.power == "usb_adapter":
        auto.append(("psu-5v2a", "5 V supply"))
    needs_boost = False
    if d.power == "lipo":
        needs_boost = rail_5v > 0 or d.board.vin_range[0] > 3.0
        auto += [("lipo-2000", "battery"), ("tp4056", "charges the battery from USB")]
        if needs_boost:
            auto.append(("mt3608", "5 V from the battery"))
            if total > BOOST_MAX_MA:
                d.issues.append(Issue("error",
                    f"Peak draw ~{total:.0f} mA is too much for a single LiPo cell through a "
                    f"boost converter (~{BOOST_MAX_MA} mA). Use mains power or lighter parts.", True))
    n = len([i for i in d.instances if i.ref.startswith("PS")])
    for pid, role in auto:
        n += 1
        d.instances.append(Instance(ref=f"PS{n}", part=PARTS[pid], role=role, auto=True))

    budget = {"rail_3v3_peak_ma": round(rail_3v3), "rail_3v3_limit_ma": d.board.reg_ma,
              "rail_5v_peak_ma": round(rail_5v), "total_peak_ma": round(total),
              "source": {"usb": f"USB port ({USB_BUDGET_MA} mA)",
                         "usb_adapter": f"5 V {ADAPTER_BUDGET_MA // 1000} A adapter",
                         "lipo": f"{BATTERY_MAH} mAh LiPo"}[d.power]}
    if d.power == "lipo":
        board_avg = d.board.active_ma
        if d.low_power:
            board_avg = d.board.active_ma * LOW_POWER_DUTY + d.board.sleep_ma * (1 - LOW_POWER_DUTY)
        avg = board_avg + sum(i.part.avg_ma for i in d.instances if i.rail)
        if needs_boost:
            avg /= BOOST_EFFICIENCY
        hours = BATTERY_MAH * BATTERY_USABLE / avg
        budget["average_ma"] = round(avg, 1)
        budget["battery_hours"] = round(hours, 1)
        if hours < 24:
            d.issues.append(Issue("warning",
                f"Estimated battery life is only ~{hours:.0f} h (average ~{avg:.0f} mA). "
                + ("Consider USB power." if d.low_power else
                   "Deep sleep between readings would stretch it a long way.")))
    d.budget = budget


def _passives(d: Design) -> None:
    def add(pid: str, qty: int, reason: str) -> None:
        q, r = d.passives.get(pid, (0, reason))
        d.passives[pid] = (q + qty, r)

    for inst in d.instances:
        p = inst.part
        if p.interface == "onewire":
            add("r4k7", 1, f"pull-up on {inst.ref} DQ")
        if p.out_5v and inst.rail == "5V":
            add("r1k", 1, f"{inst.ref} ECHO is 5 V; divider brings it to 3.3 V")
            add("r2k", 1, f"{inst.ref} ECHO divider")
        if p.interface == "neopixel":
            add("r330", 1, f"series resistor on {inst.ref} DIN")
            d.issues.append(Issue("info",
                f"{inst.ref} gets 3.3 V data on a 5 V supply. That works for most WS2812B rings; "
                f"if the first LED flickers, add a 74AHCT125 level shifter."))
        if p.interface in ("neopixel", "pwm") and "c1000u" not in d.passives:
            add("c1000u", 1, "absorbs the current surge of LEDs / servo")
    d.passives["wires"] = (1, "hook-up")


def _wire(d: Design) -> None:
    b = d.board
    five_v = {"usb": "board 5V/VBUS pin", "usb_adapter": "board 5V/VBUS pin",
              "lipo": "MT3608 OUT+"}[d.power]
    for inst in d.instances:
        p = inst.part
        if not inst.rail:
            continue
        d.wires.append((f"{inst.ref} VCC", "3V3" if inst.rail == "3V3" else five_v, ""))
        d.wires.append((f"{inst.ref} GND", "GND", ""))
        for signal, pin in inst.pins.items():
            note = ""
            if p.interface == "ultrasonic" and signal == "ECHO":
                note = "through 1k/2k divider: ECHO-1k-GPIO, GPIO-2k-GND"
            elif p.interface == "neopixel":
                note = "through 330 Ohm"
            elif p.interface == "onewire":
                note = "4.7k pull-up to 3V3"
            elif p.id == "button":
                note = "other leg to GND; internal pull-up"
            d.wires.append((f"{inst.ref} {signal}", f"GPIO{pin}", note))
        if p.interface == "i2c" and p.addr_strap and inst.address != p.i2c_addr[0]:
            d.wires.append((f"{inst.ref} addr", "3V3", p.addr_strap))

    for inst in d.parts("pump-5v"):
        relay = d.parts("relay-1ch")[0]
        d.wires.append((f"{inst.ref} +", f"{relay.ref} NO", ""))
        d.wires.append((f"{relay.ref} COM", five_v, "relay switches the pump's 5 V"))
        d.wires.append((f"{inst.ref} -", "GND", ""))

    if d.power == "lipo":
        bat, chg = d.parts("lipo-2000")[0], d.parts("tp4056")[0]
        d.wires.append((f"{bat.ref} +/-", f"{chg.ref} B+/B-", ""))
        if d.has("mt3608"):
            boost = d.parts("mt3608")[0]
            d.wires.append((f"{chg.ref} OUT+/OUT-", f"{boost.ref} IN+/IN-", ""))
            d.wires.append((f"{boost.ref} OUT+", "board 5V/VBUS pin", "set to 5.0 V first"))
            d.wires.append((f"{boost.ref} OUT-", "GND", ""))
        else:
            d.wires.append((f"{chg.ref} OUT+", "board VSYS pin", ""))
            d.wires.append((f"{chg.ref} OUT-", "GND", ""))

    if "c1000u" in d.passives:
        d.wires.append(("1000 uF +", five_v, "close to the LED ring / servo"))
        d.wires.append(("1000 uF -", "GND", ""))
