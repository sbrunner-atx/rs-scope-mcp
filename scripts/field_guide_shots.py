#!/usr/bin/env python3
"""Capture the Field Guide screenshots from a real scope.

Needs the generator output (Aux Out) wired to CH1. Every scenario starts from the
scope's own setup (snapshotted with SYSTem:SET? at the start), drives the generator,
captures the screen as PNG, records the commands and the scope's readouts in a JSON
sidecar, and the original setup is restored at the end, generator off.

    uv run python scripts/field_guide_shots.py --host 192.168.1.50
    uv run python scripts/field_guide_shots.py --host ... --only 06-uart-decode
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rs_scope_mcp.instrument import Instrument
from rs_scope_mcp.transport import encode_block

OUT = ROOT / "docs" / "field-guide" / "screenshots"

GEN_ON = "WGEN:OUTP ON"
PGEN_ON = "PGEN:PATT:STAT ON"
# Bench wiring: Aux Out -> 50 ohm feed-through terminator -> CH1 (the RTB2 inputs are 1 Mohm only,
# so the generator is told it drives 50 ohm); pattern generator P0..P3 -> logic probe D0..D3.
COMMON = ["DEV:MODE YT", "PGEN:PATT:STAT OFF", "CHAN1:STAT ON", "CHAN1:COUP DCL", "CHAN1:POS 0",
          "CHAN1:OFFS 0", "TIM:POS 0", "TIM:REF 50", "TRIG:A:SOUR CH1", "TRIG:A:TYPE EDGE",
          "TRIG:A:EDGE:SLOP POS", "WGEN:OUTP:LOAD R50", "WGEN:MOD OFF"]
LOGIC = ["CHAN1:STAT OFF", "LOG1:STAT ON", "LOG1:THR USER", "LOG1:THR:UDL 0.5",
         "TRIG:A:TYPE EDGE", "TRIG:A:MODE NORM", "TIM:REF 8.33", "BUS1:STAT ON", "BUS1:RES ON"]

SCENARIOS = [
    {
        "id": "01-loopback-sine",
        "title": "Generator loopback: 1 kHz sine with automatic measurements",
        "setup": ["WGEN:FUNC SIN", "WGEN:FREQ 1000", "WGEN:VOLT 1", "WGEN:VOLT:OFFS 0",
                  "CHAN1:SCAL 0.2", "TIM:SCAL 2e-4", "TRIG:A:MODE NORM", "TRIG:A:LEV1 0",
                  "MEAS1:SOUR CH1", "MEAS1:MAIN FREQ", "MEAS1:ENAB ON",
                  "MEAS2:SOUR CH1", "MEAS2:MAIN PEAK", "MEAS2:ENAB ON",
                  "MEAS3:SOUR CH1", "MEAS3:MAIN RMS", "MEAS3:ENAB ON", "RUN"],
        "settle": 2.0,
        "read": ["MEAS1:RES?", "MEAS2:RES?", "MEAS3:RES?", "ACQ:SRAT?"],
    },
    {
        "id": "02-square-edge",
        "title": "Edge quality: rise time and overshoot of a 100 kHz square wave",
        "setup": ["WGEN:FUNC SQU", "WGEN:FREQ 100000", "WGEN:VOLT 1", "WGEN:VOLT:OFFS 0",
                  "CHAN1:SCAL 0.2", "TIM:SCAL 5e-8", "TRIG:A:MODE NORM", "TRIG:A:LEV1 0",
                  "MEAS1:SOUR CH1", "MEAS1:MAIN RTIM", "MEAS1:ENAB ON",
                  "MEAS2:SOUR CH1", "MEAS2:MAIN POV", "MEAS2:ENAB ON",
                  "MEAS3:SOUR CH1", "MEAS3:MAIN AMPL", "MEAS3:ENAB ON", "RUN"],
        "settle": 2.0,
        "read": ["MEAS1:RES?", "MEAS2:RES?", "MEAS3:RES?"],
    },
    {
        "id": "03-fft-harmonics",
        "title": "FFT of a 10 kHz square wave: the odd harmonics",
        # FFT mode owns the timebase: enable SPEC first, then set start/stop. The scope
        # couples span, RBW, window and sample rate, so the range is applied repeatedly
        # until it settles (FW 03.000 converges in 2-3 rounds).
        "setup": ["WGEN:FUNC SQU", "WGEN:FREQ 10000", "WGEN:VOLT 1", "WGEN:VOLT:OFFS 0",
                  "CHAN1:SCAL 0.5", "TRIG:A:MODE AUTO", "TRIG:A:LEV1 0"],
        "fft": {"start": 0, "stop": 2e5, "window": "HANN"},
        "settle": 4.0,
        "read": ["SPEC:FREQ:STAR?", "SPEC:FREQ:STOP?", "SPEC:FREQ:BAND?"],
    },
    {
        "id": "04-am-envelope",
        "title": "AM: 100 kHz carrier, 1 kHz tone, 80 % depth (the envelope)",
        "setup": ["WGEN:FUNC SIN", "WGEN:FREQ 100000", "WGEN:VOLT 1", "WGEN:VOLT:OFFS 0",
                  "WGEN:MOD:TYPE AM", "WGEN:MOD:AM:FREQ 1000", "WGEN:MOD:AM:DEPT 80", "WGEN:MOD ON",
                  "CHAN1:SCAL 0.2", "TIM:SCAL 5e-4", "TRIG:A:MODE AUTO", "TRIG:A:LEV1 0",
                  "MEAS1:SOUR CH1", "MEAS1:MAIN UPE", "MEAS1:ENAB ON",
                  "MEAS2:SOUR CH1", "MEAS2:MAIN PEAK", "MEAS2:ENAB ON"],
        "single": True,
        "settle": 1.0,
        "read": ["MEAS1:RES?", "MEAS2:RES?"],
    },
    {
        "id": "05-am-sidebands",
        "title": "AM in the frequency domain: carrier and the two sidebands at +/- 1 kHz",
        "setup": ["WGEN:FUNC SIN", "WGEN:FREQ 100000", "WGEN:VOLT 1", "WGEN:VOLT:OFFS 0",
                  "WGEN:MOD:TYPE AM", "WGEN:MOD:AM:FREQ 1000", "WGEN:MOD:AM:DEPT 80", "WGEN:MOD ON",
                  "CHAN1:SCAL 0.2", "TRIG:A:MODE AUTO"],
        "fft": {"start": 9.5e4, "stop": 1.05e5, "window": "HANN"},
        "settle": 6.0,
        "read": ["SPEC:FREQ:CENT?", "SPEC:FREQ:SPAN?", "SPEC:FREQ:BAND?"],
    },
    {
        "id": "06-uart-decode",
        "title": "Serial decode: a UART byte stream with the bus table",
        # 500 Hz pulses, 15 % duty = 300 us high on an idle-low line: at 9600 Bd with
        # idle-low polarity every pulse is a start bit plus data, i.e. one byte per period.
        "setup": ["WGEN:FUNC PULS", "WGEN:FREQ 500", "WGEN:FUNC:PULS:DCYC 15", "WGEN:VOLT 1",
                  "WGEN:VOLT:OFFS 0.5", "CHAN1:SCAL 0.2", "CHAN1:POS -2", "CHAN1:THR 0.5",
                  "TIM:SCAL 2e-3", "TRIG:A:MODE AUTO", "TRIG:A:LEV1 0.5",
                  "BUS1:TYPE UART", "BUS1:UART:DATA:SOUR CH1", "BUS1:UART:BAUD 9600",
                  "BUS1:UART:SSIZ 8", "BUS1:UART:PAR NONE", "BUS1:UART:SBIT B1",
                  "BUS1:UART:POL IDLL", "BUS1:FORM HEX", "BUS1:STAT ON", "BUS1:RES ON"],
        "single": True,
        "settle": 1.0,
        "read": ["BUS1:UART:FCO?", "BUS1:UART:FRAM1:STAT?", "BUS1:UART:FRAM1:WORD1:VAL?"],
        "harvest_uart": True,
    },
    {
        "id": "07-history-segments",
        "title": "Segmented memory: a burst of single acquisitions with timestamps in the segment table",
        "setup": ["WGEN:FUNC PULS", "WGEN:FREQ 500", "WGEN:FUNC:PULS:DCYC 15", "WGEN:VOLT 1",
                  "WGEN:VOLT:OFFS 0.5", "CHAN1:SCAL 0.2", "CHAN1:POS -2", "TIM:SCAL 5e-4",
                  "TRIG:A:MODE NORM", "TRIG:A:LEV1 0.5", "ACQ:POIN:AUT OFF", "ACQ:POIN 10000",
                  "ACQ:NSIN:COUN 16"],
        "runsingle": True,
        "after": ["CHAN1:HIST:TTAB ON"],
        "settle": 1.5,
        "read": ["ACQ:AVA?", "CHAN1:HIST:STAR?", "CHAN1:HIST:STOP?"],
    },
    {
        "id": "08-bode-plot",
        "title": "Bode plot (K36): the loopback as a 0 dB / 0 degree reference sweep",
        "setup": ["ACQ:POIN:AUT ON", "BPL:INP CH1", "BPL:OUTP CH1", "BPL:FREQ:STAR 100",
                  "BPL:FREQ:STOP 1e6", "BPL:POIN:LOG 20", "BPL:AMPL:MODE CONS", "BPL:REP OFF"],
        "bode": True,
        "settle": 1.0,
        "read": ["BPL:FREQ:STAR?", "BPL:FREQ:STOP?"],
    },
    {
        "id": "09-i2c-decode",
        "title": "I2C on the logic probe: the pattern generator asks *idn? and reads the answer",
        "setup": LOGIC + ["PGEN:FUNC I2C", "TRIG:A:SOUR D0", "TRIG:A:EDGE:SLOP NEG", "TIM:SCAL 5e-5",
                          "BUS1:TYPE I2C", "BUS1:I2C:CLOC:SOUR D0", "BUS1:I2C:DATA:SOUR D1",
                          "BUS1:FORM ASC"],
        "pgen": True, "single": True, "settle": 2.0,
        "read": ["BUS1:I2C:FCO?", "BUS1:I2C:FRAM1:ADDR?", "BUS1:I2C:FRAM1:DATA?", "BUS1:I2C:FRAM2:DATA?"],
    },
    {
        "id": "10-spi-decode",
        "title": "SPI on D0..D3: clock, MOSI, MISO and chip select decoded as text",
        "setup": LOGIC + ["PGEN:FUNC SPI", "TRIG:A:SOUR D3", "TRIG:A:EDGE:SLOP NEG", "TIM:SCAL 1e-4",
                          "BUS1:TYPE SPI", "BUS1:SPI:CS:SOUR D3", "BUS1:SPI:CS:POL NEG",
                          "BUS1:SPI:CLOC:SOUR D0", "BUS1:SPI:CLOC:POL POS", "BUS1:SPI:MOSI:SOUR D1",
                          "BUS1:SPI:MISO:SOUR D2", "BUS1:SPI:SSIZ 8", "BUS1:SPI:BORD MSBF", "BUS1:FORM ASC"],
        "pgen": True, "single": True, "settle": 2.0,
        "read": ["BUS1:SPI:FCO?", "BUS1:SPI:FRAM1:DATA:MOSI?", "BUS1:SPI:FRAM2:DATA:MOSI?"],
    },
    {
        "id": "11-uart-logic",
        "title": "UART on D0: 115200 Bd, measured from the bit time, decoded as *idn?",
        "setup": LOGIC + ["PGEN:FUNC UART", "TRIG:A:SOUR D0", "TRIG:A:EDGE:SLOP NEG", "TIM:SCAL 5e-5",
                          "BUS1:TYPE UART", "BUS1:UART:DATA:SOUR D0", "BUS1:UART:POL IDLH",
                          "BUS1:UART:BAUD 115200", "BUS1:UART:SSIZ 9", "BUS1:UART:PAR NONE",
                          "BUS1:UART:SBIT B1", "BUS1:FORM ASC"],
        "pgen": True, "single": True, "settle": 2.0,
        "read": ["BUS1:UART:FCO?", "BUS1:UART:FRAM1:WCO?"],
    },
    {
        "id": "12-logic-counter",
        "title": "Logic analyzer: a 4-bit counter on D0..D3 decoded as a parallel bus",
        "setup": LOGIC + ["PGEN:FUNC COUN", "PGEN:PATT:COUN:FREQ 100000", "PGEN:PATT:COUN:DIR UPW",
                          "TRIG:A:SOUR D3", "TRIG:A:EDGE:SLOP POS", "TIM:SCAL 2e-5", "TIM:REF 50",
                          "BUS1:TYPE PAR", "BUS1:PAR:WIDT 4", "BUS1:PAR:DATA0:SOUR D0",
                          "BUS1:PAR:DATA1:SOUR D1", "BUS1:PAR:DATA2:SOUR D2", "BUS1:PAR:DATA3:SOUR D3",
                          "BUS1:FORM HEX"],
        "pgen": True, "single": True, "settle": 2.0,
        "read": ["BUS1:PAR:FCO?", "BUS1:PAR:FRAM1:DATA?", "BUS1:PAR:FRAM2:DATA?", "BUS1:PAR:FRAM3:DATA?"],
    },
]


def capture(inst: Instrument, path: Path) -> int:
    inst.write("HCOPy:LANGuage PNG", check_errors=False)
    png = inst.query_block("HCOPy:DATA?", timeout=30)
    path.write_bytes(png)
    return len(png)


def run(sc: dict, inst: Instrument, baseline: bytes) -> dict:
    log: dict = {"id": sc["id"], "title": sc["title"], "commands": [], "problems": []}
    restore(inst, baseline)
    for line in COMMON + sc["setup"]:
        r = inst.write(line)
        log["commands"].append(line)
        if not r.ok:
            log["problems"].append({"command": line, "errors": r.instrument_errors})
    if sc.get("bode"):
        for line in ("BPL:ENAB ON", "BPL:STAT RUN"):
            r = inst.write(line, confirm=True)
            log["commands"].append(line)
            if not r.ok:
                log["problems"].append({"command": line, "errors": r.instrument_errors})
        t0 = time.monotonic()
        while time.monotonic() - t0 < 120 and not inst.query("BPL:STAT?").response.upper().startswith("STOP"):
            time.sleep(0.5)
        log["sweep_seconds"] = round(time.monotonic() - t0, 1)
    else:
        on = PGEN_ON if sc.get("pgen") else GEN_ON
        r = inst.write(on, confirm=True)
        log["commands"].append(on)
        if not r.ok:
            log["problems"].append({"command": on, "errors": r.instrument_errors})
        time.sleep(0.5)
        if sc.get("single"):
            inst.write("SING", wait=True, timeout=30)
            log["commands"].append("SING")
        if sc.get("runsingle"):
            t0 = time.monotonic()
            inst.write("RUNS", wait=True, timeout=120)
            log["commands"].append("RUNS")
            log["acquire_seconds"] = round(time.monotonic() - t0, 2)
    if sc.get("fft"):
        log["fft_rounds"] = setup_fft(inst, sc["fft"], log)
    for line in sc.get("after", []):
        r = inst.write(line)
        log["commands"].append(line)
        if not r.ok:
            log["problems"].append({"command": line, "errors": r.instrument_errors})
    time.sleep(sc.get("settle", 1.0))
    vals = inst.query_many(sc.get("read", []))
    log["readouts"] = dict(zip(sc.get("read", []), vals))
    if getattr(inst, "last_errors", None):
        log["problems"].append({"readout_errors": inst.last_errors})
    if sc.get("harvest_uart"):
        log["harvest"] = harvest_uart(inst)
    path = OUT / f"{sc['id']}.png"
    log["png_bytes"] = capture(inst, path)
    log["png"] = path.name
    (OUT / f"{sc['id']}.json").write_text(json.dumps(log, indent=1))
    return log


def setup_fft(inst: Instrument, f: dict, log: dict) -> list:
    """Enable the FFT and converge start/stop (the scope couples span, RBW and timebase)."""
    for line in ("SPEC ON", "SPEC:SOUR CH1", f"SPEC:FREQ:WIND:TYPE {f['window']}", "SPEC:FREQ:MAGN:SCAL DBV"):
        r = inst.write(line)
        log["commands"].append(line)
        if not r.ok:
            log["problems"].append({"command": line, "errors": r.instrument_errors})
    rounds = []
    for _ in range(5):
        for line in (f"SPEC:FREQ:STAR {f['start']}", f"SPEC:FREQ:STOP {f['stop']}"):
            inst.write(line, check_errors=False)
        inst.drain_errors()
        time.sleep(0.5)
        got = inst.query_many(["SPEC:FREQ:STAR?", "SPEC:FREQ:STOP?", "SPEC:FREQ:BAND?"])
        rounds.append(got)
        lo, hi = float(got[0]), float(got[1])
        span = f["stop"] - f["start"]
        if abs(lo - f["start"]) < 0.05 * span and abs(hi - f["stop"]) < 0.05 * span:
            break
    inst.write("RUN", check_errors=False)
    return rounds


def harvest_uart(inst: Instrument) -> dict:
    n = int(inst.query("BUS1:UART:FCO?").response)
    lines = []
    for k in range(1, n + 1):
        lines += [f"BUS1:UART:FRAM{k}:STAR?", f"BUS1:UART:FRAM{k}:STAT?", f"BUS1:UART:FRAM{k}:WORD1:VAL?"]
    t0 = time.monotonic()
    vals = inst.query_many(lines)
    dt = time.monotonic() - t0
    frames = [{"start": vals[i], "status": vals[i + 1], "value": vals[i + 2]} for i in range(0, len(vals), 3)]
    return {"frames": n, "queries": len(lines), "seconds": round(dt, 3),
            "frames_per_second": round(n / dt, 1) if dt else None, "first": frames[:5]}


def restore(inst: Instrument, baseline: bytes) -> None:
    inst.write("WGEN:OUTP OFF", check_errors=False)
    inst.write("PGEN:PATT:STAT OFF", check_errors=False)
    inst.write("WGEN:MOD OFF", check_errors=False)
    if inst.query("DEV:MODE?").response.upper() != "YT":
        inst.write("DEV:MODE YT", check_errors=False)
    inst.transport.write_raw(b"SYST:SET " + encode_block(baseline) + b"\n")
    inst.opc(30)
    inst.drain_errors()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True)
    ap.add_argument("--only", nargs="*", help="scenario ids to run")
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    inst = Instrument(a.host, timeout=30)
    info = inst.connect()
    baseline = inst.query_block("SYST:SET?")
    form = inst.query("FORM?").response
    (OUT / "_instrument.json").write_text(json.dumps(info, indent=1))
    results = []
    try:
        for sc in SCENARIOS:
            if a.only and sc["id"] not in a.only:
                continue
            t0 = time.monotonic()
            log = run(sc, inst, baseline)
            print(f"{sc['id']:22s} {time.monotonic() - t0:5.1f}s  {log['readouts']}  "
                  f"problems={log['problems'] or '-'}")
            if "harvest" in log:
                print("   harvest:", {k: v for k, v in log["harvest"].items() if k != "first"})
            results.append(log)
    finally:
        restore(inst, baseline)
        inst.write(f"FORM {form}", check_errors=False)
        inst.write("RUN", check_errors=False)
        print("restored; generator:", inst.query("WGEN:OUTP?").response,
              "mode:", inst.query("DEV:MODE?").response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
