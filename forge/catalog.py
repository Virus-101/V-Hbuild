"""The parts the builder is allowed to use.

A language model asked to "design a device" will happily name a sensor that
does not exist, wire it to a pin the board does not break out, and power a
servo from a 3.3 V regulator. So the model never writes pins or part numbers:
it picks from this list, and the engine does the electrical work from the
facts recorded here.

Every figure below is taken from the part's datasheet or the board's
published pinout. Sizes are the module outline, rounded up, and are only
used to size the enclosure. Prices are typical single-unit hobby-retail
prices in USD and are there so the BOM has a total, not as a quote.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Board:
    id: str
    name: str
    family: str                     # "esp32" or "rp2040" - picks libraries
    pio_env: dict                   # the PlatformIO [env] block
    i2c: tuple[int, int]            # (SDA, SCL) - the Arduino core's defaults
    digital: tuple[int, ...]        # free GPIO, best first
    analog: tuple[int, ...]         # ADC-capable GPIO safe to use with Wi-Fi on
    reg_ma: int                     # 3V3 regulator rating
    self_ma: int                    # the board's own peak draw (radio on)
    active_ma: float                # typical average with Wi-Fi connected
    sleep_ma: float                 # deep sleep, whole dev board
    vin_range: tuple[float, float]  # what its 5V/VSYS pin accepts
    size_mm: tuple[float, float, float]
    usb: str
    price: float
    notes: str = ""
    chip: str = ""                  # esptool --chip, or "rp2040"


@dataclass(frozen=True)
class Part:
    id: str
    name: str
    category: str
    does: str                       # one line the planner reads
    interface: str                  # i2c | digital_in | digital_out | pwm | analog | onewire | ultrasonic | neopixel | power
    supply: str                     # "3v3", "5v" or "3v3|5v" (either works)
    peak_ma: float
    avg_ma: float
    size_mm: tuple[float, float, float]
    price: float
    i2c_addr: tuple[int, ...] = ()  # first is the default, the rest are strap options
    addr_strap: str = ""            # how to move to the next address
    out_5v: bool = False            # drives its output at 5 V when supplied from 5 V
    panel: str = ""                 # what the enclosure needs: window, vent, dome, twin_holes, cable, button, slot
    lib_deps: tuple[str, ...] = ()
    notes: str = ""


BOARDS: dict[str, Board] = {b.id: b for b in [
    Board(
        id="esp32-c3-devkitm-1",
        name="Espressif ESP32-C3-DevKitM-1",
        family="esp32",
        # Serial stays on UART0: the micro-USB port is a CP2102N bridge, not native USB.
        pio_env={"platform": "espressif32", "board": "esp32-c3-devkitm-1",
                 "framework": "arduino"},
        i2c=(8, 9),
        # 18/19 are USB, 20/21 the UART; 2, 8 and 9 are strapping pins -
        # 8 and 9 go to I2C, whose pull-ups hold them at their boot level.
        digital=(5, 6, 7, 10, 0, 1, 3, 4, 2),
        analog=(0, 1, 3, 4, 2),
        reg_ma=800, self_ma=350, active_ma=80, sleep_ma=0.5,
        vin_range=(3.6, 5.5), size_mm=(49, 26, 12), usb="USB micro-B",
        price=8.00,
        notes="Single-core RISC-V, Wi-Fi + BLE 5. Cheapest radio board here.",
        chip="esp32c3",
    ),
    Board(
        id="esp32-s3-devkitc-1",
        name="Espressif ESP32-S3-DevKitC-1",
        family="esp32",
        pio_env={"platform": "espressif32", "board": "esp32-s3-devkitc-1",
                 "framework": "arduino"},
        i2c=(8, 9),
        # 0/3/45/46 strapping, 19/20 USB, 35-37 PSRAM on octal modules,
        # 43/44 UART, 48 or 38 the on-board RGB LED depending on revision.
        digital=(11, 12, 13, 14, 15, 16, 17, 18, 21, 39, 40, 41, 42, 47),
        analog=(1, 2, 4, 5, 6, 7, 10),
        reg_ma=800, self_ma=350, active_ma=100, sleep_ma=1.0,
        vin_range=(3.6, 5.5), size_mm=(70, 26, 12), usb="USB-C (x2)",
        price=15.00,
        notes="Dual-core, lots of GPIO, native USB. Pick when pins or speed run out.",
        chip="esp32s3",
    ),
    Board(
        id="rpi-pico-w",
        name="Raspberry Pi Pico W",
        family="rp2040",
        pio_env={"platform": "https://github.com/maxgerhardt/platform-raspberrypi.git",
                 "board": "rpipicow", "framework": "arduino",
                 "board_build.core": "earlephilhower"},
        i2c=(4, 5),
        digital=(2, 3, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
                 21, 22, 0, 1),
        analog=(26, 27, 28),
        reg_ma=800, self_ma=150, active_ma=45, sleep_ma=1.3,
        vin_range=(1.8, 5.5), size_mm=(52, 21, 10), usb="USB micro-B",
        price=7.00,
        notes="VSYS takes a single LiPo cell directly. Only three ADC pins.",
        chip="rp2040",
    ),
]}


PARTS: dict[str, Part] = {p.id: p for p in [
    # --- sensing -------------------------------------------------------
    Part("bme280", "BME280 breakout (temp / humidity / pressure)", "sensor",
         "air temperature, relative humidity and barometric pressure",
         "i2c", "3v3", 1, 0.01, (15, 12, 3), 6.00,
         i2c_addr=(0x76, 0x77), addr_strap="tie SDO to 3V3 for 0x77",
         panel="vent", lib_deps=("adafruit/Adafruit BME280 Library",)),
    Part("sht31", "SHT31-D breakout (temp / humidity)", "sensor",
         "accurate air temperature and relative humidity",
         "i2c", "3v3", 1.5, 0.01, (18, 13, 3), 9.00,
         i2c_addr=(0x44, 0x45), addr_strap="tie ADR to 3V3 for 0x45",
         panel="vent", lib_deps=("adafruit/Adafruit SHT31 Library",)),
    Part("bh1750", "BH1750 ambient light sensor", "sensor",
         "ambient light level in lux",
         "i2c", "3v3", 0.2, 0.12, (19, 14, 3), 3.00,
         i2c_addr=(0x23, 0x5C), addr_strap="tie ADDR to 3V3 for 0x5C",
         panel="window", lib_deps=("claws/BH1750",)),
    Part("vl53l0x", "VL53L0X time-of-flight distance sensor", "sensor",
         "distance to an object, 30 mm - 1.2 m, laser time-of-flight",
         "i2c", "3v3", 40, 19, (25, 13, 3), 6.00,
         i2c_addr=(0x29,), panel="window", lib_deps=("pololu/VL53L0X",)),
    Part("mpu6050", "MPU-6050 accelerometer + gyroscope (GY-521)", "sensor",
         "motion, tilt, shake and orientation (6-axis IMU)",
         "i2c", "3v3", 4, 3.9, (21, 16, 3), 4.00,
         i2c_addr=(0x68, 0x69), addr_strap="tie AD0 to 3V3 for 0x69",
         lib_deps=("adafruit/Adafruit MPU6050",)),
    Part("ds18b20", "DS18B20 waterproof temperature probe (1 m)", "sensor",
         "temperature of a liquid or soil via a sealed probe on a cable",
         "onewire", "3v3", 1.5, 0.75, (10, 10, 10), 4.00,
         panel="cable",
         lib_deps=("paulstoffregen/OneWire", "milesburton/DallasTemperature"),
         notes="Needs a 4.7 kOhm pull-up from DATA to 3V3."),
    Part("soil-capacitive", "Capacitive soil moisture sensor v1.2", "sensor",
         "soil moisture (analog, corrosion-resistant capacitive probe)",
         "analog", "3v3|5v", 5, 5, (98, 23, 4), 3.00,
         panel="cable", notes="Probe sits outside the box on its lead."),
    Part("pir-hcsr501", "HC-SR501 PIR motion sensor", "sensor",
         "detects people / warm bodies moving within ~7 m",
         "digital_in", "5v", 0.065, 0.065, (33, 25, 25), 2.50,
         panel="dome", notes="Output is 3.3 V even on a 5 V supply."),
    Part("hcsr04", "HC-SR04 ultrasonic distance sensor", "sensor",
         "distance 2 cm - 4 m by sound (cheap, works on glass and water)",
         "ultrasonic", "5v", 15, 2, (45, 20, 15), 2.00,
         out_5v=True, panel="twin_holes"),
    Part("button", "12 mm tactile push button", "input",
         "a physical push button for the user",
         "digital_in", "3v3", 0, 0, (12, 12, 8), 0.30, panel="button"),
    # --- output --------------------------------------------------------
    Part("ssd1306", "0.96\" SSD1306 OLED, 128x64, I2C", "display",
         "small monochrome display for numbers, text and icons",
         "i2c", "3v3", 20, 12, (27, 28, 4), 5.00,
         i2c_addr=(0x3C, 0x3D), addr_strap="move the address resistor for 0x3D",
         panel="window",
         lib_deps=("adafruit/Adafruit SSD1306", "adafruit/Adafruit GFX Library")),
    Part("ws2812b-ring12", "WS2812B RGB LED ring, 12 LEDs", "output",
         "addressable colour lights / status ring",
         "neopixel", "5v", 720, 60, (37, 37, 3), 4.00,
         panel="window", lib_deps=("adafruit/Adafruit NeoPixel",),
         notes="Peak is all 12 LEDs white at full brightness."),
    Part("buzzer", "Active buzzer module (KY-012)", "output",
         "beeps and simple alarms",
         "digital_out", "3v3|5v", 30, 1, (19, 15, 12), 1.00, panel="vent"),
    Part("sg90", "SG90 micro servo", "actuator",
         "moves something to an angle (0-180 deg), light loads",
         "pwm", "5v", 650, 10, (23, 12, 29), 3.00, panel="slot",
         notes="Stall current ~650 mA; needs its own 5 V, never 3V3."),
    Part("relay-1ch", "1-channel 5 V relay module, 3.3 V-logic compatible", "actuator",
         "switches a mains or DC load on and off (lamp, pump, heater)",
         "digital_out", "5v", 75, 5, (50, 26, 19), 3.00, panel="cable",
         notes="Mains wiring must stay in its own compartment."),
    Part("pump-5v", "5 V submersible mini water pump + tubing", "actuator",
         "pumps water (plant watering, fountains)",
         "power", "5v", 220, 0, (45, 24, 30), 4.00, panel="cable",
         notes="Switched through the relay; not wired to a GPIO."),
    # --- power ---------------------------------------------------------
    Part("lipo-2000", "3.7 V 2000 mAh LiPo cell (JST-PH)", "power",
         "rechargeable battery", "power", "", 0, 0, (60, 36, 7), 9.00),
    Part("tp4056", "TP4056 USB-C LiPo charger with protection", "power",
         "charges the LiPo from USB and protects it", "power", "", 0, 0,
         (28, 17, 4), 1.50, panel="usb"),
    Part("mt3608", "MT3608 boost converter, set to 5.0 V", "power",
         "makes 5 V from a LiPo for parts that need it", "power", "", 0, 0,
         (37, 17, 6), 1.50, notes="Trim to 5.0 V with a meter before connecting."),
    Part("psu-5v2a", "5 V 2 A USB wall adapter + cable", "power",
         "external supply for high-current 5 V parts", "power", "", 0, 0,
         (0, 0, 0), 7.00),
]}

# Passives the engine adds itself - the planner never picks these.
PASSIVES = {
    "r4k7": ("4.7 kOhm resistor (1-Wire pull-up)", 0.05),
    "r1k": ("1 kOhm resistor (divider top)", 0.05),
    "r2k": ("2 kOhm resistor (divider bottom)", 0.05),
    "r330": ("330 Ohm resistor (LED data line)", 0.05),
    "c1000u": ("1000 uF 10 V electrolytic (across 5 V at the LEDs/servo)", 0.30),
    "wires": ("Dupont jumper wires + small perfboard", 3.00),
}

POWER_OPTIONS = ("usb", "usb_adapter", "lipo")
POWER_PARTS = {"lipo-2000", "tp4056", "mt3608", "psu-5v2a"}
USB_BUDGET_MA = 500       # USB 2.0 port, what a laptop will promise
ADAPTER_BUDGET_MA = 2000


def planner_view() -> list[dict]:
    """What the planner model sees: the choices, never the pinouts."""
    boards = [{"id": b.id, "name": b.name, "gpio_free": len(b.digital),
               "adc_pins": len(b.analog), "notes": b.notes, "price": b.price}
              for b in BOARDS.values()]
    parts = [{"id": p.id, "name": p.name, "does": p.does,
              "supply": p.supply, "price": p.price}
             for p in PARTS.values() if p.id not in POWER_PARTS]
    return [{"boards": boards}, {"parts": parts}]
