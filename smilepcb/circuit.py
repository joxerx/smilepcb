"""Versioned, synthetic circuit model shared by KiCad and firmware outputs."""
from copy import deepcopy
import uuid

NAMESPACE = uuid.UUID("94cf9dd5-4f82-4c18-bef3-12ea4ec24ae1")
RGB_BLOCK = "ws2812b-ahct-v2"


def identity(key):
    return str(uuid.uuid5(NAMESPACE, key))


def fixture_config():
    return {"schema": 1, "name": "lab2x3", "profile": "rp2040-header-3v3-v1",
            "matrix": {"rows": ["GP2", "GP3"], "cols": ["GP4", "GP5", "GP6"]},
            "available_gpio": ["GP7"], "rgb_gpio": "GP7", "supply_mv": 5000,
            "supply_budget_ma": 500, "controller_reserve_ma": 100,
            "keys": [{"row": r, "col": c, "x": 35 + c * 19.05, "y": 35 + r * 19.05}
                     for r in range(2) for c in range(3)]}


def part(ref, symbol, footprint, nets, pcb, sch, value=None, reference_text=None, no_connect=None):
    result = {"ref": ref, "symbol": symbol, "footprint": footprint,
            "value": value or symbol.split(":")[1], "nets": nets,
            "pcb": list(pcb), "sch": list(sch), "uuid": identity("symbol/" + ref)}
    if reference_text is not None:
        result["reference_text"] = reference_text
    if no_connect:
        result["no_connect"] = no_connect
    return result


def rgb_led_position(key):
    # MX footprint origin is a pin, not the stem centre. The 5.5 mm LED
    # courtyard fits the 5.85 mm gap between the switch rows at this pitch.
    return [key["x"] - 2.54, key["y"] - 4.445]


def base_parts(config):
    result = [part("J1", "Connector_Generic:Conn_02x05_Odd_Even",
        "Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical",
        {"1": "+5V", "2": "GND", "3": "ROW0", "4": "ROW1", "5": "COL0",
         "6": "COL1", "7": "COL2", "8": "RGB_DATA", "9": "unconnected-(J1-Pin_9-Pad9)", "10": "GND"},
        (15, 35), (40, 40), "RP2040 interface", no_connect=["9"])]
    for i, key in enumerate(config["keys"], 1):
        x, y = key["x"], key["y"]
        sx, sy = 80 + key["col"] * 50, 35 + key["row"] * 45
        result.append(part(f"SW{i}", "Switch:SW_Push", "Button_Switch_Keyboard:SW_Cherry_MX_1.00u_PCB",
            {"1": f"COL{key['col']}", "2": f"KEY{i}"}, (x, y), (sx, sy)))
        result.append(part(f"D{i}", "Device:D", "Diode_SMD:D_SOD-123",
            {"1": f"ROW{key['row']}", "2": f"KEY{i}"}, (x + 7, y + 8), (sx, sy + 17), "1N4148W"))
    return result


def rgb_parts(config):
    result = [
        part("U_RGB", "74xGxx:74AHCT1G125", "Package_TO_SOT_SMD:SOT-23-5",
             {"1": "GND", "2": "RGB_DATA", "3": "GND", "4": "RGB_BUFFER_OUT", "5": "+5V"},
             (16, 19), (50, 40), "SN74AHCT1G125DBV", reference_text={"offset": [0, -4], "angle": 0}),
        part("R_RGB", "Device:R", "Resistor_SMD:R_0603_1608Metric",
             {"1": "RGB_BUFFER_OUT", "2": "RGB_CHAIN_0"}, (23, 19), (83, 40), "330"),
        part("R_PD", "Device:R", "Resistor_SMD:R_0603_1608Metric",
             {"1": "RGB_DATA", "2": "GND"}, (12, 19), (25, 60), "100k"),
        part("C_RGB_BUF", "Device:C", "Capacitor_SMD:C_0603_1608Metric",
             {"1": "+5V", "2": "GND"}, (16, 23), (50, 65), "100nF",
             reference_text={"offset": [0, 4.5], "angle": 0}),
        part("C_RGB_BULK", "Device:C_Polarized", "Capacitor_SMD:CP_Elec_6.3x5.4",
             {"1": "+5V", "2": "GND"}, (24, 25), (80, 65), "100uF/10V"),
    ]
    for index, key in enumerate(config["keys"]):
        number = index + 1
        sx, sy = 110 + key["col"] * 45, 40 + key["row"] * 55
        lx, ly = rgb_led_position(key)
        result.append(part(f"LED{number}", "LED:WS2812B", "LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
             {"1": "+5V", "2": f"RGB_CHAIN_{number}" if number < len(config["keys"]) else f"unconnected-(LED{number}-DOUT-Pad2)",
              "3": "GND", "4": f"RGB_CHAIN_{index}"},
             (lx, ly), (sx, sy), reference_text={"offset": [0, 0], "angle": 0},
             no_connect=["2"] if number == len(config["keys"]) else None))
        result.append(part(f"C_LED{number}", "Device:C", "Capacitor_SMD:C_0603_1608Metric",
             {"1": "+5V", "2": "GND"}, (key["x"] + 3, ly), (sx, sy + 22), "100nF",
             reference_text={"offset": [3.1, 0], "angle": 90}))
    return result


def validate_config(config):
    if config.get("schema") != 1 or config.get("profile") != "rp2040-header-3v3-v1":
        raise ValueError("Unsupported lab profile")
    if config["matrix"] != fixture_config()["matrix"] or config["keys"] != fixture_config()["keys"]:
        raise ValueError("This lab slice supports only the synthetic 2x3 carrier")
    gpio = config["rgb_gpio"]
    used = config["matrix"]["rows"] + config["matrix"]["cols"]
    if gpio in used or gpio not in config["available_gpio"] or gpio != "GP7":
        raise ValueError("RGB GPIO is unavailable or conflicts with the matrix")
    if config["supply_mv"] != 5000:
        raise ValueError("The AHCT/WS2812B block requires a 5 V supply")
    required = len(config["keys"]) * 60 + config["controller_reserve_ma"]
    if config["controller_reserve_ma"] < 100 or config["supply_budget_ma"] < required:
        raise ValueError(f"Power budget exceeded: require at least {required} mA")
    return {"estimated_led_max_ma": len(config["keys"]) * 60,
            "controller_reserve_ma": config["controller_reserve_ma"], "required_budget_ma": required}


def qmk_config(config, rgb=False):
    result = {"keyboard_name": "SmilePCB Lab 2x3", "manufacturer": "SmilePCB Lab",
        "maintainer": "joxerx", "processor": "RP2040", "bootloader": "rp2040",
        "diode_direction": "COL2ROW", "matrix_pins": deepcopy(config["matrix"]),
        "usb": {"vid": "0xFEED", "pid": "0x0001", "device_version": "0.0.1"},
        "layouts": {"LAYOUT": {"layout": [{"matrix": [k["row"], k["col"]],
             "x": k["col"], "y": k["row"]} for k in config["keys"]]}}}
    if rgb:
        result["features"] = {"rgb_matrix": True}
        result["ws2812"] = {"pin": config["rgb_gpio"], "driver": "vendor"}
        result["rgb_matrix"] = {"driver": "ws2812", "max_brightness": 64,
            "animations": {"solid_reactive_simple": True},
            "layout": [{"matrix": [k["row"], k["col"]], "x": k["col"] * 112,
                        "y": k["row"] * 64, "flags": 4} for k in config["keys"]]}
    return result
