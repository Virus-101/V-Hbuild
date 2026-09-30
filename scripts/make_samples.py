"""Regenerate the example .vbuild files the website plays (site/samples/).

    python scripts/make_samples.py

Plans and behaviour rules are written out here rather than asked of a
model, so the examples are the same every time. Firmware is compiled when
PlatformIO is installed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vhbuild import pipeline, planner  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "site" / "samples"

SAMPLES = {
    "plant-monitor": {
        "idea": "A plant monitor that waters my basil when the soil is dry and shows the moisture on a small screen",
        "plan": {"name": "Basil Keeper", "board_id": "esp32-c3-devkitm-1", "power": "usb_adapter",
                 "summary": "Checks the soil every ten minutes and waters the basil when it is dry. "
                            "The screen shows the moisture.",
                 "low_power": False,
                 "parts": [{"part_id": "soil-capacitive", "role": "measures soil moisture"},
                           {"part_id": "pump-5v", "role": "waters the basil"},
                           {"part_id": "relay-1ch", "role": "switches the pump"},
                           {"part_id": "ssd1306", "role": "shows the moisture"}],
                 "behavior": ["Every 10 minutes, if soil moisture is below 35%, run the pump for 3 seconds"],
                 "assumptions": [], "out_of_scope": []},
        "rules": [{"every_seconds": 600, "variable": "s1_pct", "compare": "<", "threshold": 35,
                   "then": [{"do": "M2.switch_on_for", "amount": 3},
                            {"do": "serial.print", "text": "Watered at {s1_pct}%"}]}],
    },
    "motion-lamp": {
        "idea": "A desk lamp that glows red when someone walks into my office, with a button to turn it off",
        "plan": {"name": "Door Glow", "board_id": "rpi-pico-w", "power": "usb_adapter",
                 "summary": "An LED ring turns red when someone walks in. A button turns it off.",
                 "low_power": False,
                 "parts": [{"part_id": "pir-hcsr501", "role": "notices someone walking in"},
                           {"part_id": "ws2812b-ring12", "role": "glows red"},
                           {"part_id": "button", "role": "turns the light off"}],
                 "behavior": ["When motion is detected, turn the ring red", "When the button is pressed, turn it off"],
                 "assumptions": [], "out_of_scope": []},
        "rules": [{"every_seconds": 0, "variable": "s1_motion", "compare": "is true",
                   "then": [{"do": "L1.color", "text": "red"}]},
                  {"every_seconds": 0, "variable": "sw1_pressed", "compare": "is true",
                   "then": [{"do": "L1.color", "text": "off"}]}],
    },
    "weather-station": {
        "idea": "A small weather station for my balcony that shows temperature, humidity and pressure",
        "plan": {"name": "Balcony Weather", "board_id": "esp32-c3-devkitm-1", "power": "usb",
                 "summary": "Shows the temperature, humidity and air pressure on a small screen.",
                 "low_power": False,
                 "parts": [{"part_id": "bme280", "role": "reads temperature, humidity and pressure"},
                           {"part_id": "ssd1306", "role": "shows the weather"}],
                 "behavior": ["Every minute, log the readings over serial"],
                 "assumptions": [], "out_of_scope": []},
        "rules": [{"every_seconds": 60, "variable": "always", "compare": "none",
                   "then": [{"do": "serial.print", "text": "{s1_temp} C, {s1_hum} %, {s1_hpa} hPa"}]}],
    },
}


class Scripted:
    """Answers the planner and firmware calls from the plan and rules above."""
    label = "a hand-written example"

    def __init__(self, sample):
        self.sample = sample

    def structured(self, system, user, out, max_tokens=0):
        if out is planner.Plan:
            return planner.Plan(**self.sample["plan"])
        return out.model_validate({"rules": self.sample["rules"], "notes": []})


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, sample in SAMPLES.items():
        result = pipeline.run(sample["idea"], provider=Scripted(sample), log=lambda m: None)
        assert result["design"]["ok"], result["design"]["issues"]
        data = pipeline.bundle(result)
        (OUT / f"{name}.vbuild").write_bytes(data)
        print(f"{name}.vbuild  {len(data) // 1024} KB  firmware: {result['firmware_binary'] or 'source only'}")


if __name__ == "__main__":
    main()
