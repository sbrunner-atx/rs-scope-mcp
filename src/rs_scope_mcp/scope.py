"""Layer 3: typed, bench-verified operations on top of the SCPI session.

Each method encodes what was learned on a real RTB2004 (FW 03.000) so callers do not
have to: exclusive application modes, silent rejection of decode queries on the wrong
bus type, full-memory reads only when stopped, UINT scaling, FFT range convergence,
Bode polling, history segment clamping, and 9.91E+37 meaning "no value".

Everything returns plain Python / numpy data; nothing here talks to the store or the
MCP layer.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .instrument import Instrument, InstrumentError

NO_VALUE = 9.9e37  # the scope reports 9.91E+37 when there is nothing to measure


def num(s: str | None) -> float | None:
    """Parse a numeric response; None for empty, unparseable or 'no value' (9.91E+37)."""
    if s is None:
        return None
    try:
        v = float(str(s).strip().split(",")[0])
    except ValueError:
        return None
    return None if abs(v) >= NO_VALUE else v


def ints(s: str | None) -> list[int]:
    if not s:
        return []
    out = []
    for x in str(s).split(","):
        x = x.strip()
        if x:
            try:
                out.append(int(float(x)))
            except ValueError:
                pass
    return out


@dataclass
class Series:
    """A sampled waveform: values[i] is at time t0 + i*dt."""
    source: str
    kind: str          # analog | pod | digital | spectrum | bode
    t0: float
    dt: float
    values: np.ndarray
    unit: str
    meta: dict = field(default_factory=dict)

    @property
    def n(self) -> int:
        return int(self.values.shape[0])


# Frame fields read per protocol: (query tail, key, parser)
FRAME_FIELDS: dict[str, list[tuple[str, str, str]]] = {
    "I2C": [("STATus?", "status", "s"), ("STARt?", "start", "f"), ("STOP?", "stop", "f"),
            ("ADDRess?", "address", "i"), ("ACCess?", "access", "s"), ("BCOunt?", "count", "i"),
            ("DATA?", "data", "l")],
    "SPI": [("STATus?", "status", "s"), ("STARt?", "start", "f"), ("STOP?", "stop", "f"),
            ("WCOunt?", "count", "i"), ("DATA:MOSI?", "mosi", "l"), ("DATA:MISO?", "miso", "l")],
    "UART": [("STATe?", "status", "s"), ("STARt?", "start", "f"), ("STOP?", "stop", "f"),
             ("WCOunt?", "count", "i")],
    "CAN": [("STATus?", "status", "s"), ("STARt?", "start", "f"), ("STOP?", "stop", "f"),
            ("TYPE?", "type", "s"), ("IDValue?", "id", "s"), ("IDTYpe?", "id_type", "s"),
            ("DLCValue?", "dlc", "i"), ("DATA?", "data", "l")],
    "LIN": [("STATus?", "status", "s"), ("STARt?", "start", "f"), ("STOP?", "stop", "f"),
            ("IDValue?", "id", "s"), ("BCOunt?", "count", "i"), ("DATA?", "data", "l")],
    "PARallel": [("STATe?", "status", "s"), ("STARt?", "start", "f"), ("STOP?", "stop", "f"),
                 ("DATA?", "data", "l")],
    "CPARallel": [("STATe?", "status", "s"), ("STARt?", "start", "f"), ("STOP?", "stop", "f"),
                  ("DATA?", "data", "l")],
}
# BUS<b>:TYPE? short form -> query prefix that is valid for it
BUS_QUERY = {"I2C": "I2C", "SPI": "SPI", "SSPI": "SPI", "UART": "UART", "CAN": "CAN", "LIN": "LIN",
             "PAR": "PARallel", "CPAR": "CPARallel"}
SOURCES = {f"CH{i}" for i in range(1, 5)}


class ScopeError(RuntimeError):
    pass


class Scope:
    def __init__(self, inst: Instrument):
        self.i = inst

    # ------------------------------------------------------------------ helpers
    def q(self, line: str) -> str:
        return self.i.query(line).response

    def qm(self, lines: list[str]) -> list[str | None]:
        return self.i.query_many(lines)

    def w(self, line: str, confirm: bool = False) -> list[str]:
        r = self.i.write(line, confirm=confirm)
        return r.instrument_errors

    def _with_format(self, fmt: str):
        scope = self

        class _Fmt:
            def __enter__(self):
                self.prev = scope.q("FORMat?")
                scope.w(f"FORMat {fmt}")
                scope.w("FORMat:BORDer MSBF")

            def __exit__(self, *exc):
                scope.i.write(f"FORMat {self.prev}", check_errors=False)

        return _Fmt()

    # ------------------------------------------------------------------ modes and setup
    def mode(self) -> str:
        return self.q("DEVice:MODE?").strip().upper()

    def ensure_yt(self) -> None:
        """Leave FFT/Bode/XY applications; they block decode and other settings (-221)."""
        m = self.mode()
        if m == "FFT":
            self.w("SPECtrum OFF")
        elif m not in ("YT", "ZOOM"):
            self.w("DEVice:MODE YT")

    def snapshot(self) -> bytes:
        """The complete setup as the scope's own block (SYSTem:SET?, about 5 kB)."""
        return self.i.query_block("SYSTem:SET?")

    def restore(self, blob: bytes) -> list[str]:
        from .transport import encode_block
        self.i.write("WGENerator:OUTPut OFF", check_errors=False)
        self.i.write("PGENerator:PATTern:STATe OFF", check_errors=False)
        self.i.transport.write_raw(b"SYSTem:SET " + encode_block(blob) + b"\n")
        self.i.opc(30)
        return self.i.drain_errors()

    def settings(self) -> dict:
        """A compact, human-readable view of the setup that matters for a capture."""
        keys = {
            "mode": "DEVice:MODE?", "acquisition": "ACQuire:STATe?", "timebase_s_div": "TIMebase:SCALe?",
            "time_position_s": "TIMebase:POSition?", "sample_rate": "ACQuire:SRATe?",
            "record_length": "ACQuire:POINts?", "trigger_mode": "TRIGger:A:MODE?",
            "trigger_source": "TRIGger:A:SOURce?", "trigger_type": "TRIGger:A:TYPE?",
            "trigger_level_v": "TRIGger:A:LEVel1?", "pod1": "LOGic1:STATe?", "pod2": "LOGic2:STATe?",
            "bus1_type": "BUS1:TYPE?", "bus1_on": "BUS1:STATe?", "bus2_type": "BUS2:TYPE?",
            "bus2_on": "BUS2:STATe?", "generator": "WGENerator:OUTPut?",
            "pattern_generator": "PGENerator:PATTern:STATe?",
        }
        for c in range(1, 5):
            keys[f"ch{c}_on"] = f"CHANnel{c}:STATe?"
            keys[f"ch{c}_v_div"] = f"CHANnel{c}:SCALe?"
            keys[f"ch{c}_offset_v"] = f"CHANnel{c}:OFFSet?"
            keys[f"ch{c}_coupling"] = f"CHANnel{c}:COUPling?"
        vals = self.qm(list(keys.values()))
        out = {}
        for k, v in zip(keys, vals):
            if v is None:
                continue
            f = num(v)
            out[k] = f if (f is not None and not k.endswith(("_type", "coupling", "source", "mode",
                                                                 "acquisition", "trigger_type"))) else v
        return out

    # ------------------------------------------------------------------ acquisition
    def single(self, timeout: float = 30.0) -> float:
        t0 = time.monotonic()
        self.i.write("SINGle", wait=True, timeout=timeout)
        return time.monotonic() - t0

    def run(self) -> None:
        self.w("RUN")

    def stop(self) -> None:
        self.w("STOP")

    def sample_rate(self) -> float | None:
        return num(self.q("ACQuire:SRATe?"))

    # ------------------------------------------------------------------ analog
    def analog(self, ch: int, points: str = "MAX") -> Series:
        """Analog channel as volts. UINT,16 is lossless for the 10-bit ADC (bench-verified)."""
        if self.q(f"CHANnel{ch}:STATe?").strip() not in ("1", "ON"):
            raise ScopeError(f"CH{ch} is off")
        with self._with_format("UINT,16"):
            self.w(f"CHANnel{ch}:DATA:POINts {points}")
            hdr = self.q(f"CHANnel{ch}:DATA:HEADer?").split(",")
            yor, yinc = (num(v) or 0.0 for v in self.qm([f"CHANnel{ch}:DATA:YORigin?",
                                                          f"CHANnel{ch}:DATA:YINCrement?"]))
            raw = self.i.query_block(f"CHANnel{ch}:DATA?", timeout=120)
        codes = np.frombuffer(raw, dtype=">u2")
        x0, x1, n = float(hdr[0]), float(hdr[1]), int(float(hdr[2]))
        dt = (x1 - x0) / max(n, 1)
        volts = (yor + yinc * codes.astype(np.float64)).astype(np.float32)
        return Series(f"CH{ch}", "analog", x0, dt, volts, "V",
                      {"points": points, "yorigin": yor, "yincrement": yinc, "codes_max": int(codes.max(initial=0))})

    # ------------------------------------------------------------------ logic
    def logic_pod(self, pod: int = 1, points: str = "MAX") -> Series:
        """A logic pod, one byte per sample, bit n = channel n of the pod (D0..D7 for pod 1)."""
        if self.q(f"LOGic{pod}:STATe?").strip() not in ("1", "ON"):
            raise ScopeError(f"logic pod {pod} is off (LOG{pod}:STAT ON)")
        with self._with_format("UINT,8"):
            self.w(f"LOGic{pod}:DATA:POINts {points}")
            hdr = self.q(f"LOGic{pod}:DATA:HEADer?").split(",")
            raw = self.i.query_block(f"LOGic{pod}:DATA?", timeout=120)
        x0, x1, n = float(hdr[0]), float(hdr[1]), int(float(hdr[2]))
        vals = np.frombuffer(raw, dtype=np.uint8).copy()
        first = 8 * (pod - 1)
        return Series(f"D{first}-D{first + 7}", "pod", x0, (x1 - x0) / max(n, 1), vals, "bits",
                      {"pod": pod, "first_channel": first})

    # ------------------------------------------------------------------ bus decode
    def bus_type(self, bus: int) -> str:
        return self.q(f"BUS{bus}:TYPE?").strip().upper()

    def bus_bitrate(self, bus: int, typ: str) -> float | None:
        q = {"UART": f"BUS{bus}:UART:BAUDrate?", "CAN": f"BUS{bus}:CAN:BITRate?",
             "LIN": f"BUS{bus}:LIN:BITRate?"}.get(typ)
        return num(self.q(q)) if q else None

    def decode(self, bus: int = 1, max_frames: int | None = None) -> dict:
        """Harvest every decoded frame of a bus (batched field reads, ~200 frames/s)."""
        typ = self.bus_type(bus)
        proto = BUS_QUERY.get(typ)
        if not proto:
            raise ScopeError(f"BUS{bus} has unsupported type {typ}")
        warnings = []
        if self.q(f"BUS{bus}:STATe?").strip() not in ("1", "ON"):
            warnings.append(f"BUS{bus} decode is off; enabled it (BUS{bus}:STAT ON)")
            self.w(f"BUS{bus}:STATe ON")
        rate = self.bus_bitrate(bus, typ)
        sr = self.sample_rate()
        if rate and sr and sr < 10 * rate:
            warnings.append(f"sample rate {sr:.3g} Sa/s is under 10x the bit rate {rate:.3g}; "
                            "decodes may be garbled (bench-verified with segmented memory)")
        fco = f"BUS{bus}:{proto}:FCOunt?"
        try:
            n = int(num(self.q(fco)) or 0)
        except InstrumentError as e:
            raise ScopeError(f"frame count rejected for BUS{bus} ({typ}): {e}") from None
        total = n
        if max_frames is not None:
            n = min(n, max_frames)
        fields = FRAME_FIELDS[proto]
        lines = [f"BUS{bus}:{proto}:FRAMe{k}:{tail}" for k in range(1, n + 1) for tail, _, _ in fields]
        t0 = time.monotonic()
        vals = self.qm(lines) if lines else []
        frames: list[dict] = []
        for k in range(n):
            row = vals[k * len(fields):(k + 1) * len(fields)]
            fr: dict = {"index": k + 1}
            for (_, key, kind), v in zip(fields, row):
                fr[key] = {"s": (lambda x: x.strip() if x else None), "f": num,
                           "i": (lambda x: int(num(x)) if num(x) is not None else None), "l": ints}[kind](v)
            frames.append(fr)
        if proto == "UART":
            self._uart_words(bus, frames)
        return {"bus": bus, "type": typ, "protocol": proto, "frames": frames, "frame_count": total,
                "harvested": len(frames), "seconds": round(time.monotonic() - t0, 3),
                "bitrate": rate, "sample_rate": sr, "warnings": warnings}

    def _uart_words(self, bus: int, frames: list[dict]) -> None:
        lines, owners = [], []
        for fr in frames:
            for o in range(1, (fr.get("count") or 0) + 1):
                lines += [f"BUS{bus}:UART:FRAMe{fr['index']}:WORD{o}:VALue?",
                          f"BUS{bus}:UART:FRAMe{fr['index']}:WORD{o}:STATe?"]
                owners.append(fr)
        vals = self.qm(lines) if lines else []
        for fr in frames:
            fr["data"], fr["word_status"] = [], []
        for j, fr in enumerate(owners):
            v, st = vals[2 * j], vals[2 * j + 1]
            fr["data"].append(int(num(v)) if num(v) is not None else None)
            fr["word_status"].append(st)

    # ------------------------------------------------------------------ history (segmented memory)
    def history(self, source: str = "CHANnel1") -> dict:
        avail = int(num(self.q("ACQuire:AVAilable?")) or 0)
        start, stop = (int(num(v) or 0) for v in self.qm([f"{source}:HISTory:STARt?", f"{source}:HISTory:STOP?"]))
        rel = [num(x) for x in self.q(f"{source}:HISTory:TSRelative:ALL?").split(",") if x.strip()]
        return {"available": avail, "first": start, "last": stop, "relative_times": rel}

    def select_segment(self, k: int, source: str = "CHANnel1") -> int:
        """Select a history segment (0 = newest). The scope clamps silently; returns the real one."""
        self.w(f"{source}:HISTory:CURRent {k}")
        return int(num(self.q(f"{source}:HISTory:CURRent?")) or 0)

    # ------------------------------------------------------------------ measurements
    def measure(self, source: str = "CH1", types: list[str] | None = None, settle: float = 1.0) -> dict:
        types = types or ["FREQ", "PEAK", "RMS"]
        if len(types) > 8:
            raise ScopeError("at most 8 measurements at once")
        for k, t in enumerate(types, start=1):
            for line in (f"MEASurement{k}:SOURce {source}", f"MEASurement{k}:MAIN {t}",
                         f"MEASurement{k}:ENABle ON"):
                errs = self.w(line)
                if errs:
                    raise ScopeError(f"{line}: {errs}")
        time.sleep(settle)
        vals = self.qm([f"MEASurement{k}:RESult?" for k in range(1, len(types) + 1)])
        return {t: num(v) for t, v in zip(types, vals)}

    # ------------------------------------------------------------------ spectrum (FFT)
    def spectrum(self, source: str = "CH1", start: float = 0.0, stop: float = 1e6,
                 window: str = "HANN", rounds: int = 5) -> Series:
        """FFT mode owns the timebase: enable first, then converge start/stop (bench-verified)."""
        for line in ("SPECtrum ON", f"SPECtrum:SOURce {source}", f"SPECtrum:FREQuency:WINDow:TYPE {window}",
                     "SPECtrum:FREQuency:MAGNitude:SCALe DBV"):
            self.w(line)
        span = max(stop - start, 1.0)
        got = [start, stop]
        for _ in range(rounds):
            self.i.write(f"SPECtrum:FREQuency:STARt {start}", check_errors=False)
            self.i.write(f"SPECtrum:FREQuency:STOP {stop}", check_errors=False)
            self.i.drain_errors()
            time.sleep(0.4)
            got = [num(v) or 0.0 for v in self.qm(["SPECtrum:FREQuency:STARt?", "SPECtrum:FREQuency:STOP?"])]
            if abs(got[0] - start) < 0.05 * span and abs(got[1] - stop) < 0.05 * span:
                break
        self.run()
        time.sleep(1.5)
        rbw = num(self.q("SPECtrum:FREQuency:BANDwidth?"))
        with self._with_format("REAL,32"):
            hdr = self.q("SPECtrum:WAVeform:SPECtrum:DATA:HEADer?").split(",")
            raw = self.i.query_block("SPECtrum:WAVeform:SPECtrum:DATA?", timeout=60)
        mags = np.frombuffer(raw, dtype=">f4").astype(np.float32)
        f0, f1, n = float(hdr[0]), float(hdr[1]), int(float(hdr[2]))
        return Series(f"FFT({source})", "spectrum", f0, (f1 - f0) / max(n - 1, 1), mags, "dBV",
                      {"rbw_hz": rbw, "requested": [start, stop], "applied": got, "window": window})

    # ------------------------------------------------------------------ Bode plot (K36)
    def bode(self, start: float, stop: float, points_per_decade: int = 20, input_ch: str = "CH1",
             output_ch: str = "CH2", confirm: bool = False, timeout: float = 300) -> dict:
        """Run a Bode sweep. Drives the generator output, so it needs confirm=True."""
        for line in (f"BPLot:INPut {input_ch}", f"BPLot:OUTPut {output_ch}", f"BPLot:FREQuency:STARt {start}",
                     f"BPLot:FREQuency:STOP {stop}", f"BPLot:POINts:LOGarithmic {points_per_decade}",
                     "BPLot:REPeat OFF"):
            errs = self.w(line)
            if errs:
                raise ScopeError(f"{line}: {errs}")
        self.i.write("BPLot:ENABle ON", confirm=confirm)
        t0 = time.monotonic()
        self.i.write("BPLot:STATe RUN", confirm=confirm)
        while time.monotonic() - t0 < timeout:
            if self.q("BPLot:STATe?").strip().upper().startswith("STOP"):
                break
            time.sleep(0.5)
        seconds = time.monotonic() - t0
        with self._with_format("REAL,32"):
            f, g, p = (np.frombuffer(self.i.query_block(q, timeout=60), dtype=">f4").astype(np.float64)
                       for q in ("BPLot:FREQuency:DATA?", "BPLot:GAIN:DATA?", "BPLot:PHASe:DATA?"))
        return {"freq_hz": f, "gain_db": g, "phase_deg": p, "seconds": round(seconds, 1),
                "input": input_ch, "output": output_ch, "load": self.q("WGENerator:OUTPut:LOAD?").strip()}

    # ------------------------------------------------------------------ screen
    def screenshot(self) -> bytes:
        self.i.write("HCOPy:LANGuage PNG", check_errors=False)
        return self.i.query_block("HCOPy:DATA?", timeout=30)
