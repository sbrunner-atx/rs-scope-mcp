"""Demo mode: the oscilloscope in the background of every science-fiction lab scene.

Loops through scenes that show what the RTB2 can do: a heartbeat monitor, glowing noisy
signals, chirps, AM and FM, spectra, bursts, and, with the logic probe wired, live
I2C/SPI decoding next to the analog trace. Everything comes from the scope's own
generators, so the only wiring is:

  analog: Aux Out -> 50 ohm feed-through terminator -> CH1
  full:   plus pattern generator P0..P3 -> logic probe D0..D3 (probe ground to the
          pattern generator's ground contact)

The original setup is snapshotted first and restored at the end, generators off.
See docs/DEMO.md.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .instrument import Instrument
from .scope import Scope
from .transport import encode_block

HEART_FILE = "RSHEART.CSV"   # uploaded to /INT for the heartbeat scene, removed afterwards

COMMON = [
    "MASK:TEST STOP", "MASK:STAT OFF", "DEV:MODE YT", "SPEC OFF", "WGEN:MOD OFF", "WGEN:SWE OFF", "WGEN:BURS OFF", "WGEN:NOIS:REL 0",
    "WGEN:OUTP:LOAD R50", "WGEN:ARB:VIS OFF", "PGEN:PATT:STAT OFF", "LOG1:STAT OFF", "BUS1:STAT OFF",
    "BUS2:STAT OFF", "CHAN1:STAT ON", "CHAN1:COUP DCL", "CHAN1:OFFS 0", "CHAN1:POS 0", "TIM:POS 0",
    "TIM:REF 50", "TIM:ZOOM:STAT OFF", "TRIG:A:SOUR CH1", "TRIG:A:TYPE EDGE", "TRIG:A:EDGE:SLOP POS",
    "TRIG:A:LEV1 0", "TRIG:A:MODE AUTO", "TRIG:A:EDGE:FILT:HFR OFF", "DISP:PERS OFF", "CHAN1:WCOL DEF", "ACQ:TYPE REFR",
]
LOGIC = ["LOG1:STAT ON", "LOG1:THR USER", "LOG1:THR:UDL 0.5", "BUS1:STAT ON", "BUS1:RES ON",
         "BUS1:FORM ASC"]


@dataclass
class Scene:
    id: str
    title: str
    caption: str
    level: str                   # "analog" or "full"
    setup: list[str]
    generator: bool = True       # Aux Out on
    pattern: bool = False        # pattern generator on
    heart: bool = False          # needs the heartbeat waveform loaded
    fft: dict | None = None      # {"start":..,"stop":..,"window":..}
    settle: float = 3.0
    after: list[str] = field(default_factory=list)


SCENES: list[Scene] = [
    Scene("heartbeat", "Vital signs", "A heartbeat at 72 bpm, played from the generator's arbitrary memory.",
          "analog", ["CHAN1:SCAL 0.2", "CHAN1:POS -1.5", "TIM:SCAL 0.2", "WGEN:FREQ 1.2", "WGEN:VOLT 1.2",
                     "WGEN:VOLT:OFFS 0", "TRIG:A:LEV1 0.2", "TRIG:A:MODE NORM", "CHAN1:WCOL FIRE"],
          heart=True, settle=4.0),
    Scene("glow", "Signal in the noise", "A 10 kHz carrier buried in noise, intensity-graded with persistence.",
          "analog", ["WGEN:FUNC SIN", "WGEN:FREQ 10000", "WGEN:VOLT 1", "WGEN:NOIS:REL 25", "CHAN1:SCAL 0.2",
                     "TIM:SCAL 5e-5", "DISP:PERS:TYPE TIME", "DISP:PERS:TIME 1", "DISP:PERS ON",
                     "CHAN1:WCOL FIRE"], settle=4.0),
    Scene("ping", "Radar ping", "A repeating sinc pulse: the shape of a band-limited radar or sonar ping.",
          "analog", ["WGEN:FUNC SINC", "WGEN:FREQ 20000", "WGEN:VOLT 1.2", "CHAN1:SCAL 0.2", "CHAN1:POS -1",
                     "TIM:SCAL 2e-5", "TRIG:A:LEV1 0.3", "TRIG:A:MODE NORM", "DISP:PERS:TYPE TIME",
                     "DISP:PERS:TIME 0.5", "DISP:PERS ON", "CHAN1:WCOL RAIN"], settle=3.0),
    Scene("am", "Amplitude modulation", "A 100 kHz carrier, 1 kHz tone, 80 % depth: the classic AM envelope.",
          "analog", ["WGEN:FUNC SIN", "WGEN:FREQ 100000", "WGEN:VOLT 1", "WGEN:MOD:TYPE AM", "WGEN:MOD:AM:FREQ 1000",
                     "WGEN:MOD:AM:DEPT 80", "WGEN:MOD ON", "CHAN1:SCAL 0.2", "TIM:SCAL 5e-4",
                     "CHAN1:WCOL TEMP"], settle=3.0),
    Scene("fm-spectrum", "FM spectrum", "FM at 100 kHz: the Bessel sidebands, live on the spectrum analyzer.",
          "analog", ["WGEN:FUNC SIN", "WGEN:FREQ 100000", "WGEN:VOLT 1", "WGEN:MOD:TYPE FM", "WGEN:MOD:FM:FREQ 1000",
                     "WGEN:MOD:FM:DEV 2400", "WGEN:MOD ON", "CHAN1:SCAL 0.2"],
          fft={"start": 9.0e4, "stop": 1.1e5, "window": "HANN"}, settle=5.0),
    Scene("harmonics", "Harmonics", "A 10 kHz square wave and its odd harmonics in the frequency domain.",
          "analog", ["WGEN:FUNC SQU", "WGEN:FREQ 10000", "WGEN:VOLT 1", "CHAN1:SCAL 0.5"],
          fft={"start": 0, "stop": 2e5, "window": "HANN"}, settle=5.0),
    Scene("burst", "Burst", "Bursts of a 50 kHz sine with a zoom window on the leading edge.",
          "analog", ["WGEN:FUNC SIN", "WGEN:FREQ 50000", "WGEN:VOLT 1", "WGEN:BURS:NCYC 20",
                     "WGEN:BURS:ITIM 0.0006", "WGEN:BURS:TRIG CONT", "WGEN:BURS ON", "CHAN1:SCAL 0.2",
                     "TIM:SCAL 2e-4", "TRIG:A:LEV1 0.2", "TRIG:A:MODE NORM", "TIM:ZOOM:STAT ON",
                     "TIM:ZOOM:SCAL 2e-5", "CHAN1:WCOL RAIN"], settle=3.0),
    Scene("mask", "Pass / fail", "A tolerance mask learned from a clean sine; then noise arrives and the "
          "violations start counting.", "analog",
          ["WGEN:FUNC SIN", "WGEN:FREQ 2000", "WGEN:VOLT 1", "CHAN1:SCAL 0.2", "TIM:SCAL 2e-4",
           "TRIG:A:LEV1 0", "TRIG:A:MODE NORM", "TRIG:A:EDGE:FILT:HFR ON"],
          # the 5 kHz HF-reject in the trigger path keeps noise from triggering on the falling slope
          after=["MASK LEARN CH1 0.02 0.15", "MASK START", "WAIT 2", "WGEN:NOIS:REL 9"], settle=6.0),
    Scene("bus-i2c", "Bus traffic", "I2C on the logic probe, decoded to text, next to a carrier on CH1.",
          "full", ["WGEN:FUNC SIN", "WGEN:FREQ 20000", "WGEN:VOLT 0.6", "CHAN1:SCAL 0.2", "CHAN1:POS 2.5",
                   "CHAN1:WCOL FIRE", "PGEN:FUNC I2C", "TIM:SCAL 1e-4", "TIM:REF 8.33", "TRIG:A:SOUR D0",
                   "TRIG:A:EDGE:SLOP NEG", "TRIG:A:MODE NORM", *LOGIC, "BUS1:TYPE I2C",
                   "BUS1:I2C:CLOC:SOUR D0", "BUS1:I2C:DATA:SOUR D1"],
          pattern=True, settle=3.0),
    Scene("bus-spi", "SPI link", "SPI clock, MOSI, MISO and chip select on D0..D3, decoded as text.",
          "full", ["CHAN1:STAT OFF", "PGEN:FUNC SPI", "TIM:SCAL 1e-4", "TIM:REF 8.33", "TRIG:A:SOUR D3",
                   "TRIG:A:EDGE:SLOP NEG", "TRIG:A:MODE NORM", *LOGIC, "BUS1:TYPE SPI", "BUS1:SPI:CS:SOUR D3",
                   "BUS1:SPI:CS:POL NEG", "BUS1:SPI:CLOC:SOUR D0", "BUS1:SPI:CLOC:POL POS",
                   "BUS1:SPI:MOSI:SOUR D1", "BUS1:SPI:MISO:SOUR D2", "BUS1:SPI:SSIZ 8"],
          generator=False, pattern=True, settle=3.0),
    Scene("counter", "Logic analyzer", "A 4-bit counter on D0..D3 decoded as a parallel bus.",
          "full", ["CHAN1:STAT OFF", "PGEN:FUNC COUN", "PGEN:PATT:COUN:FREQ 100000", "PGEN:PATT:COUN:DIR UPW",
                   "TIM:SCAL 2e-5", "TRIG:A:SOUR D3", "TRIG:A:MODE NORM", *LOGIC, "BUS1:FORM HEX",
                   "BUS1:TYPE PAR", "BUS1:PAR:WIDT 4", "BUS1:PAR:DATA0:SOUR D0", "BUS1:PAR:DATA1:SOUR D1",
                   "BUS1:PAR:DATA2:SOUR D2", "BUS1:PAR:DATA3:SOUR D3"],
          generator=False, pattern=True, settle=3.0),
]


def heartbeat_csv(points: int = 2000) -> bytes:
    """One ECG period (P wave, QRS complex, T wave) in the scope's waveform CSV format."""
    t = np.linspace(0, 1, points, endpoint=False)

    def g(mu: float, s: float, a: float) -> np.ndarray:
        return a * np.exp(-0.5 * ((t - mu) / s) ** 2)

    y = g(0.18, 0.025, 0.12) + g(0.30, 0.008, -0.15) + g(0.32, 0.010, 1.0) + g(0.345, 0.009, -0.25) \
        + g(0.56, 0.045, 0.30)
    rows = "\n".join(f"{a:.7E},{b:.6E}" for a, b in zip(t * 1e-3, y))
    return ("in s,C1 in V\n" + rows + "\n").encode()


def scenes_for(level: str) -> list[Scene]:
    return [s for s in SCENES if level == "full" or s.level == "analog"]


class Demo:
    """Run the scenes on an instrument. Thread-safe stop via stop()."""

    def __init__(self, inst: Instrument, level: str = "analog", dwell: float = 12.0, loops: int = 1,
                 shots: Path | None = None, only: list[str] | None = None, log=print):
        if level not in ("analog", "full"):
            raise ValueError("level must be 'analog' or 'full'")
        self.i, self.scope = inst, Scope(inst)
        self.level, self.dwell, self.loops, self.shots, self.log = level, dwell, loops, shots, log
        self.scenes = [s for s in scenes_for(level) if not only or s.id in only]
        self._stop = threading.Event()
        self.current: str | None = None
        self.problems: list[dict] = []

    def stop(self) -> None:
        self._stop.set()

    def _w(self, line: str, confirm: bool = False) -> None:
        r = self.i.write(line, confirm=confirm)
        if not r.ok:
            self.problems.append({"scene": self.current, "command": line, "errors": r.instrument_errors})

    def _upload_heart(self) -> None:
        self.i.write('MMEMory:CDIRectory "/INT"')
        self.i.check(f'MMEMory:DATA "{HEART_FILE}",#10')
        self.i.transport.write_raw(f'MMEMory:DATA "{HEART_FILE}",'.encode() + encode_block(heartbeat_csv()) + b"\n")
        self.i.opc(30)
        self.i.drain_errors()

    def _load_heart(self) -> None:
        # the generator must be in ARB mode before the file is opened (bench-verified)
        self._w("WGEN:FUNC ARB")
        self._w(f'WGEN:ARB:FILE:NAME "/INT/{HEART_FILE}"')
        self._w("WGEN:ARB:FILE:OPEN")
        self._w("WGEN:ARB:VIS OFF")

    def _fft(self, f: dict) -> None:
        for line in ("SPEC ON", "SPEC:SOUR CH1", f"SPEC:FREQ:WIND:TYPE {f['window']}", "SPEC:FREQ:MAGN:SCAL DBV"):
            self._w(line)
        span = f["stop"] - f["start"]
        for _ in range(5):
            self.i.write(f"SPEC:FREQ:STAR {f['start']}", check_errors=False)
            self.i.write(f"SPEC:FREQ:STOP {f['stop']}", check_errors=False)
            self.i.drain_errors()
            time.sleep(0.4)
            lo, hi = (float(v or 0) for v in self.i.query_many(["SPEC:FREQ:STAR?", "SPEC:FREQ:STOP?"]))
            if abs(lo - f["start"]) < 0.05 * span and abs(hi - f["stop"]) < 0.05 * span:
                break

    def play(self, sc: Scene) -> None:
        self.current = sc.id
        for line in COMMON:
            self.i.write(line, check_errors=False)
        self.i.drain_errors()
        if sc.heart:
            self._load_heart()
        for line in sc.setup:
            self._w(line)
        if sc.generator:
            self._w("WGEN:OUTP ON", confirm=True)
        if sc.pattern:
            self._w("PGEN:PATT:STAT ON", confirm=True)
        if sc.fft:
            self._fft(sc.fft)
        self._w("RUN")
        for line in sc.after:
            if line.startswith("WAIT "):
                self._stop.wait(float(line.split()[1]))
            elif line.startswith("MASK LEARN "):
                _, _, src, xw, yw = line.split()
                self.scope.mask_create(src, float(xw), float(yw))
                self._w("MASK:RES:COUN")
            elif line == "MASK START":
                try:
                    self.scope.mask_start()
                except Exception as e:  # noqa: BLE001
                    self.problems.append({"scene": self.current, "command": line, "errors": [str(e)]})
            else:
                self._w(line)
        self.log(f"[{sc.id}] {sc.title}: {sc.caption}")
        self._stop.wait(sc.settle)
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)
            (self.shots / f"demo-{sc.id}.png").write_bytes(self.scope.screenshot())

    def run(self) -> dict:
        baseline = self.scope.snapshot()
        uploaded = False
        t0 = time.monotonic()
        played = 0
        try:
            if any(s.heart for s in self.scenes):
                self._upload_heart()
                uploaded = True
            loop = 0
            while not self._stop.is_set() and (self.loops <= 0 or loop < self.loops):
                for sc in self.scenes:
                    if self._stop.is_set():
                        break
                    self.play(sc)
                    played += 1
                    self._stop.wait(max(0.0, self.dwell - sc.settle))
                loop += 1
        finally:
            self.current = None
            errs = self.scope.restore(baseline)
            if uploaded:
                self.i.write('MMEMory:CDIRectory "/INT"', check_errors=False)
                self.i.write(f'MMEMory:DELete "{HEART_FILE}"', confirm=True, check_errors=False)
            self.i.drain_errors()
        return {"scenes_played": played, "seconds": round(time.monotonic() - t0, 1), "restored": not errs,
                "problems": self.problems}
