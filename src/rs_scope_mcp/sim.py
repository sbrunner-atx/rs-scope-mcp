"""A simulated RTB2 that speaks SCPI over TCP.

Purpose: exercise every layer (transport, registry, instrument, MCP tools, data
store) without hardware, in tests and CI, and for demos.

Behaviour is driven by the command registry, so the simulator "knows" all ~950
documented headers without per-command code:
  * set commands are validated and stored; queries return the stored value or
    the documented *RST default (enum values in short form, like the real unit)
  * unknown headers / bad suffixes push SCPI errors into SYSTem:ERRor?
A small set of handlers produce realistic data: waveforms (ASCII/REAL/UINT
with headers), logic channels, I2C/UART decode frames, measurements,
screenshots (PNG), the Bode plot arrays, and *OPC?.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import re
import socketserver
import struct
import threading
import zlib
from collections import deque

from .registry import Registry, enum_match, parse_number, short_form
from .transport import encode_block

IDN = "Rohde&Schwarz,RTB2004,1333.1005K04/999999,03.000"
OPTIONS = "B1,B6,K1,K2,K3,K15,K36"
H_DIVS = 12  # RTB2 horizontal divisions

# A deterministic I2C transaction log and UART text for decode tests.
I2C_FRAMES = [
    {"address": 0x50 << 1, "data": [0x00, 0x10], "start": -2.0e-4, "stop": -1.6e-4},  # write ptr
    {"address": (0x50 << 1) | 1, "data": [69, 158, 174, 161], "start": -1.2e-4, "stop": -0.4e-4},
    {"address": 0x68 << 1, "data": [0x3B], "start": 0.1e-4, "stop": 0.4e-4},  # IMU register read
]
UART_TEXT = b"$GPGGA,AE5VG*47\r\n"
SPI_FRAMES = [b"Rohde&Schwarz\x00", b"RTB2004\x00"]
# bus types (short form as the scope reports it) -> the query prefix that is valid for it
BUS_QUERY = {"I2C": "I2C", "SPI": "SPI", "SSPI": "SPI", "UART": "UART", "CAN": "CAN", "LIN": "LIN",
             "PAR": "PARallel", "CPAR": "CPARallel"}


class SimState:
    def __init__(self, registry: Registry):
        self.reg = registry
        self.values: dict[tuple, str] = {}
        self.errors: deque[str] = deque(maxlen=32)
        self.lock = threading.Lock()
        self.segments = 1
        self.current_segment = 0
        self.acq_state = "RUN"
        self.reset()

    def reset(self) -> None:
        self.values.clear()
        self.errors.clear()
        self.set("BUS1:TYPE", "I2C")
        self.set("BUS2:TYPE", "UART")
        self.set("CHAN1:STAT", "ON")
        self.set("MEAS1:MAIN", "FREQ")
        self.set("MEAS1:SOUR", "CH1")

    # --------------------------------------------------------------- state helpers
    def key(self, header: str, suffixes: dict) -> tuple:
        return (header, tuple(sorted(suffixes.items())))

    def set(self, line: str, value: str) -> None:
        r = self.reg.resolve(f"{line} {value}")
        if r:
            self.values[self.key(r.command.header, r.suffixes)] = value

    def get(self, line: str, default: str = "") -> str:
        r = self.reg.resolve(line if line.endswith("?") else line + "?")
        if not r:
            return default
        return self.values.get(self.key(r.command.header, r.suffixes), self.rst(r.command) or default)

    @staticmethod
    def rst(cmd) -> str | None:
        for p in cmd.parameters:
            if "rst" in p:
                v = p["rst"].split()[0] if p["rst"] else ""
                if p.get("enum"):
                    e = enum_match(v, p["enum"])
                    return short_form(e) if e else v
                return v
        return None

    def push_error(self, code: int, msg: str) -> None:
        self.errors.append(f'{code},"{msg}"')


class Handler(socketserver.StreamRequestHandler):
    state: SimState  # injected

    def handle(self) -> None:
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("latin-1").strip()
            if not line:
                continue
            out = []
            for part in split_commands(line):
                resp = self.execute(part.strip().lstrip(":"))
                if resp is not None:
                    out.append(resp)
            if out:
                if len(out) == 1 and isinstance(out[0], bytes):
                    self.wfile.write(out[0] + b"\n")
                else:
                    text = ";".join(o.decode("latin-1") if isinstance(o, bytes) else o for o in out)
                    self.wfile.write(text.encode("latin-1") + b"\n")
            self.wfile.flush()

    # --------------------------------------------------------------- dispatcher
    def execute(self, cmd: str):
        st = self.state
        with st.lock:
            head = cmd.split()[0] if cmd else ""
            up = head.upper()
            if up == "*IDN?":
                return IDN
            if up == "*OPT?":
                return OPTIONS
            if up in ("*OPC?",):
                return "1"
            if up in ("*CLS",):
                st.errors.clear()
                return None
            if up in ("*RST", "SYST:PRES", "SYSTEM:PRESET"):
                st.reset()
                return None
            if up.startswith("SYST") and "ERR" in up and up.endswith("?"):
                if "ALL" in up:
                    errs = list(st.errors) or ['0,"No error"']
                    st.errors.clear()
                    return ",".join(errs)
                return st.errors.popleft() if st.errors else '0,"No error"'
            if up in ("SYST:TREE?", "SYSTEM:TREE?"):
                return ";".join(sorted({h for c in st.reg.commands for h in c.all_headers}))
            if up in ("SYST:SET?", "SYSTEM:SET?"):
                blob = json.dumps([[list(k[0:1]) + [list(map(list, k[1]))], v] for k, v in st.values.items()])
                return encode_block(base64.b64encode(blob.encode()))
            if up in ("SYST:SET", "SYSTEM:SET"):
                arg = cmd.split(None, 1)[1].strip() if " " in cmd else ""
                try:
                    payload = arg[2 + int(arg[1]):]
                    items = json.loads(base64.b64decode(payload))
                    st.values = {(k[0], tuple(tuple(x) for x in k[1])): v for k, v in items}
                except Exception:  # noqa: BLE001
                    st.push_error(-161, "Invalid block data")
                return None
            if up in ("SING", "SINGLE", "RUNS", "RUNSINGLE"):
                st.acq_state = "COMP"
                if up.startswith("RUNS"):
                    n = int(parse_number(st.get("ACQ:NSIN:COUN", "1")) or 1)
                    st.segments, st.current_segment = max(1, n), 0
                return None
            if up == "RUN":
                st.acq_state = "RUN"
                return None
            if up == "STOP":
                st.acq_state = "STOP"
                return None

            r = st.reg.resolve(cmd)
            if r is None:
                st.push_error(-113, f"Undefined header;{head}")
                return None
            if r.errors:
                code = -114 if any("suffix" in e for e in r.errors) else -109
                st.push_error(code, r.errors[0])
                return None
            if r.warnings and r.command.kind != "undocumented":
                st.push_error(-224, "Illegal parameter value")
                return None
            hdr = r.command.header
            # like the real scope: a decode query for another bus type is rejected silently
            m = re.match(r"^BUS<b>:([A-Za-z0-9]+):(?:RX:|TX:)?(FRAM|FCO)", r.header_matched)
            if m:
                bus_type = st.get(f"BUS{r.suffixes.get('b', 1)}:TYPE", "PAR").upper()
                if BUS_QUERY.get(bus_type, bus_type).upper() != m.group(1).upper():
                    st.push_error(-200, "Execution error")
                    return None
            # couplings the real instrument applies on its own
            if not r.is_query and r.args:
                a0 = r.args[0].strip().upper()
                if hdr == "SPECtrum[:STATe]":
                    st.set("DEV:MODE", "FFT" if a0 in ("ON", "1") else "YT")
                if hdr == "BPLot:ENABle":
                    st.set("DEV:MODE", "BPL" if a0 in ("ON", "1") else "YT")
                    if a0 in ("ON", "1"):
                        st.set("WGEN:OUTP:LOAD", "R50")
                leaving_bode = st.get("DEV:MODE?", "YT").upper() == "BPL"
                if hdr == "DEVice:MODE" and a0 != "BPL" and leaving_bode:
                    st.set("WGEN:OUTP", "OFF")  # leaving the Bode application turns the generator off
            data = self.data_query(r.header_matched, r)
            if data is not None:
                return data
            if r.is_query:
                k = st.key(hdr, r.suffixes)
                if k in st.values:
                    return st.values[k]
                v = st.rst(r.command)
                return v if v is not None else "0"
            if r.args:
                p = r.command.parameters[0] if r.command.parameters else {}
                v = r.args[0]
                if p.get("enum"):
                    e = enum_match(v, p["enum"])
                    v = short_form(e) if e else v
                st.values[st.key(hdr, r.suffixes)] = ",".join([v, *r.args[1:]])
            return None

    # --------------------------------------------------------------- data handlers
    def fmt(self) -> tuple[str, int]:
        f = self.state.get("FORM", "ASC")
        parts = [p.strip().upper() for p in f.split(",")]
        kind = parts[0][:4]
        bits = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else (32 if kind == "REAL" else 8)
        return kind, bits

    def timebase(self) -> tuple[float, float, int]:
        scale = parse_number(self.state.get("TIM:SCAL", "100e-6")) or 1e-4
        n = 1200
        return -scale * H_DIVS / 2, scale * H_DIVS / 2, n

    def analog(self, ch: int) -> list[float]:
        x0, x1, n = self.timebase()
        f = 1000.0 * ch
        amp = 1.0 / ch
        return [amp * math.sin(2 * math.pi * f * (x0 + (x1 - x0) * i / n)) + 0.01 * math.sin(i * 7.3)
                for i in range(n)]

    def logic(self, bit: int) -> list[int]:
        _, _, n = self.timebase()
        period = 2 ** (bit + 3)
        return [1 if (i // period) % 2 == 0 else 0 for i in range(n)]

    def encode_values(self, vals: list[float], vscale: float = 1.0) -> bytes | str:
        kind, bits = self.fmt()
        order = self.state.get("FORM:BORD", "MSBF").upper()
        endian = "<" if order.startswith("LSB") else ">"
        if kind == "REAL":
            return encode_block(struct.pack(f"{endian}{len(vals)}f", *vals))
        if kind == "UINT":
            top = (1 << bits) - 1
            codes = [max(0, min(top, round((v / (vscale * 10) + 0.5) * top))) for v in vals]
            fmtc = {8: "B", 16: "H", 32: "I"}[bits]
            return encode_block(struct.pack(f"{endian}{len(codes)}{fmtc}", *codes))
        return ",".join(f"{v:.6g}" for v in vals)

    def data_query(self, hdr: str, r):
        s = r.suffixes
        x0, x1, n = self.timebase()
        if hdr == "CHANnel<m>:DATA?":
            return self.encode_values(self.analog(s.get("m", 1)))
        if hdr in ("CHANnel<m>:DATA:HEADer?", "DIGital<m>:DATA:HEADer?", "LOGic<p>:DATA:HEADer?"):
            return f"{x0:.4E},{x1:.4E},{n},1"
        if hdr == "CHANnel<m>:DATA:POINts" and r.is_query:
            return str(n)
        if hdr == "CHANnel<m>:DATA:XINCrement?":
            return f"{(x1 - x0) / n:.6E}"
        if hdr == "CHANnel<m>:DATA:XORigin?":
            return f"{x0:.6E}"
        if hdr == "CHANnel<m>:DATA:YINCrement?":
            _kind, bits = self.fmt()
            return f"{10.0 / ((1 << bits) - 1):.6E}"
        if hdr == "CHANnel<m>:DATA:YORigin?":
            return "-5.0E+00"
        if hdr == "DIGital<m>:DATA?" or hdr == "LOGic<p>:DATA?":
            if hdr.startswith("DIG"):
                return self.encode_values([float(b) for b in self.logic(s.get("m", 1))])
            pod = s.get("p", 1)
            bits = [self.logic(b + 8 * (pod - 1)) for b in range(8)]
            words = [sum(bits[b][i] << b for b in range(8)) for i in range(n)]
            kind, _ = self.fmt()
            if kind == "UINT":  # pods are raw bitmasks, one byte per sample (bench-verified)
                return encode_block(bytes(words))
            return self.encode_values([float(w) for w in words])
        if hdr == "HCOPy:DATA?":
            return encode_block(tiny_png())
        st = self.state
        if hdr == "ACQuire:STATe" and r.is_query:
            return st.acq_state
        if hdr == "ACQuire:AVAilable?":
            return str(st.segments)
        hq = hdr.rstrip("?")
        if hq.endswith(":HISTory:STARt") and r.is_query:
            return str(-(st.segments - 1))
        if hq.endswith(":HISTory:STOP") and r.is_query:
            return "0"
        if hdr.endswith(":HISTory:CURRent"):
            if r.is_query:
                return str(st.current_segment)
            k = int(parse_number(r.args[0]) or 0) if r.args else 0
            st.current_segment = max(-(st.segments - 1), min(0, k))  # clamps silently, like the scope
            return None
        if hdr.endswith(":HISTory:TSRelative:ALL?"):
            return ",".join(f"{-0.25 * k:.6E}" for k in range(st.segments))
        if hdr.endswith(":HISTory:TSRelative?"):
            return f"{0.25 * st.current_segment:.6E}"
        if hdr.endswith(":HISTory:TSABsolute:ALL?"):
            return ",".join(f"12,0,{30 - 0.25 * k:.6E}" for k in range(st.segments))
        if hdr.endswith(":HISTory:TSDate:ALL?"):
            return ",".join("2026,11,23" for _ in range(st.segments))
        if hdr == "BPLot:STATe" and r.is_query:
            return "STOP"
        if hdr.startswith("SPECtrum:WAVeform:SPECtrum:DATA"):
            f0, f1, npts = 0.0, 2e5, 400
            freqs = [f0 + (f1 - f0) * i / (npts - 1) for i in range(npts)]
            if hdr.endswith("HEADer?"):
                return f"{f0:.6E},{f1:.6E},{npts},1"
            if hdr.endswith("DATA?"):
                # square wave: odd harmonics of 10 kHz, -20 dBV at the fundamental
                mags = []
                for f in freqs:
                    v = -100.0
                    for h in range(1, 20, 2):
                        if abs(f - 1e4 * h) < 300:
                            v = max(v, -20 - 20 * math.log10(h))
                    mags.append(v)
                return self.encode_values(mags)
        if hdr.startswith(("BUS<b>:SPI:FCOunt", "BUS<b>:SSPI:FCOunt")):
            return str(len(SPI_FRAMES))
        if hdr.startswith("BUS<b>:SPI:FRAMe<n>"):
            n = s.get("n", 1)
            if not 1 <= n <= len(SPI_FRAMES):
                st.push_error(-222, "Data out of range;frame index")
                return None
            fr = SPI_FRAMES[n - 1]
            tail = hdr.split("FRAMe<n>:")[1]
            return {"STATus?": "OK", "STARt?": f"{-1e-4 + 6e-4 * (n - 1):.6E}",
                    "STOP?": f"{-1e-4 + 6e-4 * (n - 1) + 4e-4:.6E}", "WCOunt?": str(len(fr)),
                    "DATA:MOSI?": ",".join(str(b) for b in fr),
                    "DATA:MISO?": ",".join(str(b) for b in b"\x00" + fr[:-1])}.get(tail, "0")
        if hdr == "ACQuire:SRATe?":
            return f"{n / (x1 - x0):.4E}"
        if hdr == "MEASurement<m>:RESult[:ACTual]?":
            typ = self.state.get(f"MEAS{s.get('m', 1)}:MAIN", "FREQ").upper()
            src = self.state.get(f"MEAS{s.get('m', 1)}:SOUR", "CH1").upper()
            ch = int(src[2]) if src.startswith("CH") else 1
            return {"FREQ": f"{1000.0 * ch:.6E}", "PER": f"{1 / (1000.0 * ch):.6E}",
                    "PEAK": f"{2.0 / ch:.6E}", "RMS": f"{0.7071 / ch:.6E}"}.get(typ[:4], "9.91E+37")
        # ---- bus decode (I2C on BUS1, UART on BUS2 by default)
        if hdr == "BUS<b>:I2C:FCOunt?":
            return str(len(I2C_FRAMES))
        if hdr.startswith("BUS<b>:I2C:FRAMe<n>"):
            fr = self._frame(I2C_FRAMES, s.get("n", 1))
            if fr is None:
                return None
            tail = hdr.split("FRAMe<n>:")[1]
            if tail.startswith("BYTE<o>"):
                o = s.get("o", 1)
                if o > len(fr["data"]):
                    self.state.push_error(-222, "Data out of range")
                    return "0"
                return {"BYTE<o>:VALue?": str(fr["data"][o - 1]), "BYTE<o>:ACCess?": "OK",
                        "BYTE<o>:COMPlete?": "1", "BYTE<o>:ACKStart?": "0", "BYTE<o>:STARt?": "0"}.get(tail, "0")
            return {
                "DATA?": ",".join(str(b) for b in fr["data"]),
                "STATus?": "OK", "STARt?": f"{fr['start']:.6E}", "STOP?": f"{fr['stop']:.6E}",
                "ADDRess?": str(fr["address"]), "ADEVice?": str(fr["address"] >> 1),
                "ACCess?": "READ" if fr["address"] & 1 else "WRITe", "AMODe?": "BIT7",
                "BCOunt?": str(len(fr["data"])), "ACOMplete?": "1", "AACCess?": "OK",
                "ASTart?": f"{fr['start']:.6E}", "ADBStart?": f"{fr['start']:.6E}",
            }.get(tail, "0")
        if hdr in ("BUS<b>:UART:FCOunt?", "BUS<b>:UART:RX:FCOunt?"):
            return "1"
        if hdr == "BUS<b>:UART:TX:FCOunt?":
            return "0"
        if hdr.startswith("BUS<b>:UART:") and "FRAMe<n>" in hdr:
            tail = hdr.split("FRAMe<n>:")[1]
            if tail == "WCOunt?":
                return str(len(UART_TEXT))
            if tail.startswith("WORD<o>"):
                o = s.get("o", 1)
                if o > len(UART_TEXT):
                    self.state.push_error(-222, "Data out of range")
                    return "0"
                if "VAL" in tail:
                    return str(UART_TEXT[o - 1])
                if "STAT" in tail:
                    return "OK"
                if "STAR" in tail or "STOP" in tail:
                    return f"{-5e-4 + o * 1e-4:.6E}"
                if "SOUR" in tail:
                    return "RX"
            return {"STARt?": "-5.0E-04", "STOP?": "1.3E-03", "STATe?": "OK"}.get(tail, "0")
        # ---- Bode plot arrays
        if hdr in ("BPLot:FREQuency:DATA?", "BPLot:GAIN:DATA?", "BPLot:PHASe:DATA?"):
            f0, f1, npts = 10.0, 100e3, 50
            freqs = [f0 * (f1 / f0) ** (i / (npts - 1)) for i in range(npts)]
            fc = 3000.0
            if "FREQ" in hdr:
                vals = freqs
            elif "GAIN" in hdr:
                vals = [-10 * math.log10(1 + (f / fc) ** 4) for f in freqs]  # 2nd-order low-pass
            else:
                vals = [-math.degrees(math.atan2(math.sqrt(2) * f / fc, 1 - (f / fc) ** 2)) for f in freqs]
            return self.encode_values(vals)
        return None

    def _frame(self, frames, n):
        if not 1 <= n <= len(frames):
            self.state.push_error(-222, "Data out of range;frame index")
            return None
        return frames[n - 1]


def split_commands(line: str) -> list[str]:
    parts, cur, q = [], [], False
    for ch in line:
        if ch == '"':
            q = not q
        if ch == ";" and not q:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p for p in (x.strip() for x in parts) if p]


def tiny_png(w: int = 8, h: int = 6) -> bytes:
    """A valid w x h PNG (dark grid with a yellow trace), no dependencies."""
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            on_trace = y == int((h - 1) * (0.5 - 0.4 * math.sin(2 * math.pi * x / w)))
            row += bytes((250, 230, 30) if on_trace else (20, 20, 25))
        rows.append(bytes(row))

    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


class SimServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start(host: str = "127.0.0.1", port: int = 0, registry: Registry | None = None):
    """Start a simulator in a background thread. Returns (server, port)."""
    state = SimState(registry or Registry.default())
    handler = type("BoundHandler", (Handler,), {"state": state})
    srv = SimServer((host, port), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run a simulated RTB2 SCPI server.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5025)
    a = ap.parse_args(argv)
    srv, port = start(a.host, a.port)
    print(f"simulated RTB2 listening on {a.host}:{port} (Ctrl-C to stop)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
