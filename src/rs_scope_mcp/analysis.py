"""Layer 5 (part): compact summaries of harvested data.

The rule for everything that goes back to the assistant: summaries up, details on
request, always with counts and pointers. These functions turn megabytes of samples
and hundreds of frames into a few hundred tokens of facts.
"""
from __future__ import annotations

from collections import Counter

import numpy as np


def _r(x: float | None, digits: int = 4) -> float | None:
    if x is None or not np.isfinite(x):
        return None
    return float(f"{x:.{digits}g}")


def analog_summary(values: np.ndarray, dt: float, codes_max: int | None = None) -> dict:
    """Levels, RMS, dominant frequency (rising mean crossings) and clipping."""
    v = values.astype(np.float64, copy=False)
    if v.size == 0:
        return {"n": 0}
    vmin, vmax, mean = float(v.min()), float(v.max()), float(v.mean())
    rms = float(np.sqrt(np.mean(v * v)))
    ac_rms = float(v.std())
    out: dict = {"n": int(v.size), "duration_s": _r(v.size * dt), "min_v": _r(vmin), "max_v": _r(vmax),
                 "vpp": _r(vmax - vmin), "mean_v": _r(mean), "rms_v": _r(rms), "ac_rms_v": _r(ac_rms)}
    # frequency from rising crossings of the midpoint with hysteresis
    mid, hyst = (vmax + vmin) / 2, 0.1 * (vmax - vmin)
    if vmax - vmin > 1e-6:
        above = v > mid + hyst
        below = v < mid - hyst
        state = np.where(above, 1, np.where(below, -1, 0))
        nz = state[state != 0]
        idx = np.flatnonzero(state)
        if nz.size > 2:
            rises = idx[1:][(nz[1:] == 1) & (nz[:-1] == -1)]
            if rises.size >= 2:
                period = float(np.median(np.diff(rises))) * dt
                out["frequency_hz"] = _r(1 / period) if period > 0 else None
                out["cycles"] = int(rises.size)
    if codes_max is not None and codes_max >= 65000:
        out["clipping"] = True
    elif vmax - vmin > 0:
        top = np.mean(v >= vmax - 1e-9)
        bot = np.mean(v <= vmin + 1e-9)
        if top > 0.02 and bot > 0.02 and "frequency_hz" not in out:
            out["clipping_suspected"] = True
    return out


def pod_edges(values: np.ndarray, t0: float, dt: float, first_channel: int = 0,
              max_edges: int = 0) -> dict:
    """Per-channel edge statistics from pod bytes (bit n = channel first_channel+n)."""
    chans = {}
    for bit in range(8):
        b = ((values >> bit) & 1).astype(np.int8)
        d = np.diff(b)
        idx = np.flatnonzero(d)
        name = f"D{first_channel + bit}"
        info: dict = {"edges": int(idx.size), "high_fraction": _r(float(b.mean()), 3)}
        if idx.size == 0:
            info["level"] = int(b[0]) if b.size else None
        else:
            if idx.size > 1:
                widths = np.diff(idx) * dt
                info["min_pulse_s"] = _r(float(widths.min()))
                info["median_pulse_s"] = _r(float(np.median(widths)))
                if widths.min() > 0:
                    # NRZ data (UART, CAN): bit rate = 1 / shortest pulse.
                    # A clock line toggles twice per period: frequency = 1 / (2 x typical pulse).
                    info["nrz_bitrate_estimate"] = _r(1 / float(widths.min()))
                    info["clock_hz_if_clock"] = _r(1 / (2 * float(np.median(widths))))
            info["first_edge_s"] = _r(t0 + (idx[0] + 1) * dt)
            if max_edges:
                info["edges_s"] = [_r(t0 + (i + 1) * dt, 6) for i in idx[:max_edges]]
        chans[name] = info
    active = {k: v for k, v in chans.items() if v["edges"]}
    idle = {k: v.get("level") for k, v in chans.items() if not v["edges"]}
    return {"n": int(values.size), "duration_s": _r(values.size * dt), "active": active, "idle_levels": idle}


def _ascii(data: list[int]) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data if b is not None and 0 <= b < 256)


def frames_summary(decoded: dict, preview: int = 5) -> dict:
    frames = decoded.get("frames", [])
    status = Counter(str(f.get("status")) for f in frames)
    out: dict = {"bus": decoded.get("bus"), "type": decoded.get("type"), "frames": decoded.get("frame_count"),
                 "harvested": len(frames), "status": dict(status), "seconds": decoded.get("seconds")}
    if decoded.get("warnings"):
        out["warnings"] = decoded["warnings"]
    for key in ("address", "id"):
        c = Counter(f.get(key) for f in frames if f.get(key) is not None)
        if c:
            out[f"{key}es" if key == "address" else "ids"] = [
                {key: (f"0x{a:02X}" if isinstance(a, int) else a), "frames": n} for a, n in c.most_common(10)]
    payload = []
    for f in frames:
        payload.extend(x for x in (f.get("data") or f.get("mosi") or []) if x is not None)
    out["bytes"] = len(payload)
    text = _ascii(payload)
    if text and sum(ch != "." for ch in text) > 0.6 * len(text):
        out["ascii"] = text[:200]
    bad = [f["index"] for f in frames if str(f.get("status", "OK")).upper() not in ("OK", "NONE")]
    if bad:
        out["non_ok_frames"] = bad[:20]

    def slim(f: dict) -> dict:
        s = {k: v for k, v in f.items() if v not in (None, [], "") and k != "word_status"}
        for k in ("data", "mosi", "miso"):
            if k in s and isinstance(s[k], list):
                s[k] = " ".join(f"{b:02X}" for b in s[k][:32] if b is not None) + (" …" if len(s[k]) > 32 else "")
        return s

    out["first"] = [slim(f) for f in frames[:preview]]
    if len(frames) > preview:
        out["last"] = [slim(f) for f in frames[-2:]]
    return out


def bode_summary(f: np.ndarray, g: np.ndarray, p: np.ndarray) -> dict:
    if f.size == 0:
        return {"points": 0}
    i_pk = int(np.argmax(g))
    ref = float(np.median(g[: max(3, f.size // 10)]))  # low-frequency gain as the reference
    out: dict = {"points": int(f.size), "range_hz": [_r(float(f[0])), _r(float(f[-1]))],
                 "peak_gain_db": _r(float(g[i_pk])), "peak_at_hz": _r(float(f[i_pk])),
                 "low_freq_gain_db": _r(ref), "high_freq_gain_db": _r(float(np.median(g[-max(3, f.size // 10):])))}
    for name, level in (("minus_3db", ref - 3.0), ("minus_6db", ref - 6.0)):
        below = np.flatnonzero(g < level)
        if below.size and below[0] > 0:
            k = below[0]
            # interpolate on a log-frequency axis
            x0, x1 = np.log10(f[k - 1]), np.log10(f[k])
            y0, y1 = g[k - 1], g[k]
            x = x0 + (level - y0) * (x1 - x0) / (y1 - y0) if y1 != y0 else x0
            out[f"{name}_hz"] = _r(float(10 ** x))
            out[f"phase_at_{name}_deg"] = _r(float(np.interp(x, np.log10(f), p)))
    hi = out["high_freq_gain_db"] or 0
    lo = out["low_freq_gain_db"] or 0
    if g.max() - max(lo, hi) > 3:
        out["shape"] = "band-pass or resonant"
    elif lo - hi > 6:
        out["shape"] = "low-pass"
    elif hi - lo > 6:
        out["shape"] = "high-pass"
    else:
        out["shape"] = "flat within 6 dB"
    return out


def spectrum_peaks(f0: float, df: float, mags: np.ndarray, n: int = 10, min_prominence_db: float = 10) -> dict:
    m = mags.astype(np.float64)
    if m.size < 3:
        return {"points": int(m.size)}
    floor = float(np.median(m))
    idx = np.flatnonzero((m[1:-1] > m[:-2]) & (m[1:-1] >= m[2:])) + 1
    idx = idx[m[idx] > floor + min_prominence_db]
    idx = idx[np.argsort(m[idx])[::-1]][:n]
    peaks = sorted(({"freq_hz": _r(f0 + i * df), "dbv": _r(float(m[i]), 3)} for i in idx),
                   key=lambda d: d["freq_hz"])
    return {"points": int(m.size), "range_hz": [_r(f0), _r(f0 + (m.size - 1) * df)],
            "noise_floor_dbv": _r(floor, 3), "peaks": peaks}


def minmax_decimate(values: np.ndarray, t0: float, dt: float, t_start: float | None, t_end: float | None,
                    max_points: int = 400) -> dict:
    """A window of a series reduced to at most max_points [t, min, max] rows."""
    n = values.size
    i0 = 0 if t_start is None else max(0, int((t_start - t0) / dt))
    i1 = n if t_end is None else min(n, int((t_end - t0) / dt) + 1)
    seg = values[i0:i1]
    if seg.size == 0:
        return {"rows": []}
    step = max(1, int(np.ceil(seg.size / max_points)))
    rows = []
    for j in range(0, seg.size, step):
        chunk = seg[j:j + step]
        rows.append([_r(t0 + (i0 + j) * dt, 6), _r(float(chunk.min())), _r(float(chunk.max()))])
    return {"window_s": [_r(t0 + i0 * dt, 6), _r(t0 + i1 * dt, 6)], "samples": int(seg.size),
            "samples_per_row": step, "columns": ["t_s", "min", "max"], "rows": rows}


# ---------------------------------------------------------------------------------- envelope
def envelope(values: np.ndarray, dt: float, carrier_hz: float | None = None) -> dict:
    """Envelope of a modulated or keyed carrier: AM depth, rise/fall time, crest factor.

    The envelope is the peak of |v - mean| over windows of about two carrier periods. AM depth
    m = (Emax - Emin) / (Emax + Emin). Rise and fall are 10-90 % times of the first full
    transition of the envelope (CW keying shape).
    """
    v = values.astype(np.float64, copy=False)
    v = v - float(np.mean(v))
    if v.size < 16:
        return {"n": int(v.size)}
    if not carrier_hz:
        carrier_hz = analog_summary(values, dt).get("frequency_hz")
    if not carrier_hz:
        return {"error": "no carrier found; pass carrier_hz"}
    win = max(2, round(2.0 / (carrier_hz * dt)))
    n = v.size // win
    if n < 8:
        return {"error": "record too short for this carrier; use a longer timebase"}
    env = np.abs(v[: n * win]).reshape(n, win).max(axis=1)
    edt = win * dt
    e_max, e_min = float(np.percentile(env, 99)), float(np.percentile(env, 1))
    out: dict = {"carrier_hz": _r(carrier_hz), "window_s": _r(edt), "points": int(n),
                 "envelope_max_v": _r(e_max), "envelope_min_v": _r(e_min),
                 "am_depth": _r((e_max - e_min) / (e_max + e_min), 3) if e_max + e_min > 0 else None,
                 "crest_factor": _r(float(np.max(np.abs(v)) / np.sqrt(np.mean(v * v))), 3)}
    lo, hi = e_min + 0.1 * (e_max - e_min), e_min + 0.9 * (e_max - e_min)
    if e_max - e_min > 0.2 * e_max:
        def first_cross(arr: np.ndarray, level: float, rising: bool, start: int = 0) -> int | None:
            seg = arr[start:]
            idx = np.flatnonzero(seg >= level) if rising else np.flatnonzero(seg <= level)
            return int(idx[0]) + start if idx.size else None

        # rising edge: last time below 10 % before first time above 90 %
        i_hi = first_cross(env, hi, True, first_cross(env, lo, False) or 0)
        if i_hi is not None:
            below = np.flatnonzero(env[:i_hi] <= lo)
            if below.size:
                out["rise_10_90_s"] = _r((i_hi - below[-1]) * edt)
            i_lo = first_cross(env, lo, False, i_hi)
            if i_lo is not None:
                above = np.flatnonzero(env[i_hi:i_lo] >= hi)
                if above.size:
                    out["fall_90_10_s"] = _r((i_lo - (i_hi + above[-1])) * edt)
    if out.get("rise_10_90_s") is not None:
        out["note"] = ("rise/fall resolution is one envelope window "
                       f"({_r(edt)} s); for CW keying, under ~1 ms rise means key clicks")
    return out


# ---------------------------------------------------------------------------------- bus identification
_STD_BAUD = [300, 1200, 2400, 4800, 9600, 14400, 19200, 28800, 38400, 57600, 76800, 100000, 115200,
             230400, 250000, 460800, 500000, 921600, 1000000, 2000000]


def identify_bus(values: np.ndarray, t0: float, dt: float, first_channel: int = 0) -> dict:
    """Guess what the active logic lines carry (I2C, SPI, UART, PWM, clock) from their edges.

    Heuristics, not proof: confirm by setting up the decoder the guess names and checking that
    frames decode with status OK.
    """
    lines = {}
    for bit in range(8):
        b = ((values >> bit) & 1).astype(np.int8)
        idx = np.flatnonzero(np.diff(b))
        if idx.size < 4:
            continue
        w = np.diff(idx) * dt
        wmin = float(w.min())
        # bit time: the mean of the shortest pulses (sampling makes them jitter by one sample)
        unit = float(np.mean(w[w < 1.5 * wmin]))
        q = w / unit
        quantized = float(np.mean(np.abs(q - np.round(q)) < 0.2))
        idle = int(b[0]) if b[0] == b[-1] else round(float(b.mean()))
        # bursts: gaps much longer than the typical pulse
        med = float(np.median(w))
        gaps = int(np.sum(w > 8 * med))
        # periodic: pulse widths alternate between at most two values (clock or PWM)
        hi_w = w[0::2] if b[idx[0] + 1] == 1 else w[1::2]
        lo_w = w[1::2] if b[idx[0] + 1] == 1 else w[0::2]
        period = float(np.median(hi_w[: min(hi_w.size, lo_w.size)] + lo_w[: min(hi_w.size, lo_w.size)])) \
            if min(hi_w.size, lo_w.size) else None
        regular = bool(hi_w.size >= 2 and lo_w.size >= 2 and np.std(hi_w) < 0.1 * np.mean(hi_w)
                       and np.std(lo_w) < 0.1 * np.mean(lo_w))
        lines[f"D{first_channel + bit}"] = {
            "bit": bit, "edges": int(idx.size), "idle": idle, "high_fraction": float(b.mean()),
            "min_pulse_s": wmin, "unit_pulse_s": unit, "median_pulse_s": med, "quantized": quantized, "bursts": gaps,
            "regular": regular, "period_s": period,
            "duty": float(np.median(hi_w) / period) if period else None,
            "first_edge": int(idx[0])}
    if not lines:
        return {"guess": "no activity", "lines": {}}
    names = list(lines)
    out: dict = {"active": names}

    def rnd(d: dict) -> dict:
        return {k: (_r(v) if isinstance(v, float) else v) for k, v in d.items() if k not in ("bit", "first_edge")}

    out["lines"] = {k: rnd(v) for k, v in lines.items()}
    # a clock-like line in bursts: many edges, constant half-period inside bursts
    clocks = [k for k, v in lines.items() if v["quantized"] > 0.9 and v["median_pulse_s"] <= 1.5 * v["min_pulse_s"]
              and v["bursts"] > 0]
    # periodic lines (clocks, PWM): constant high and low times, however long the low time is
    steady = [k for k, v in lines.items() if v["regular"]]
    guess, conf, setup = "unknown", "low", None
    if steady and len(lines) == 1:
        k = steady[0]
        v = lines[k]
        f = 1 / v["period_s"] if v["period_s"] else None
        if v["duty"] is not None and 0.4 < v["duty"] < 0.6:
            guess, conf = f"clock or square wave on {k}", "medium"
        else:
            guess, conf = f"PWM on {k}", "medium"
        out["frequency_hz"] = _r(f) if f else None
        out["duty"] = _r(v["duty"], 3) if v["duty"] is not None else None
        out["high_time_s"] = _r(v["duty"] * v["period_s"]) if v["duty"] and v["period_s"] else None
        if f and 40 <= f <= 500 and out["high_time_s"] and 0.0008 <= out["high_time_s"] <= 0.0025:
            guess += " (servo/ESC-style: 1-2 ms pulse)"
    elif clocks:
        clk = max(clocks, key=lambda k: lines[k]["edges"])
        others = [k for k in names if k != clk]
        both_idle_high = lines[clk]["idle"] == 1 and all(lines[o]["idle"] == 1 for o in others)
        # chip select: a line that holds one level while the clock toggles (frames the bursts)
        clk_bit = lines[clk]["bit"]
        clk_idx = np.flatnonzero(np.diff(((values >> clk_bit) & 1).astype(np.int8)))
        cs = []
        for o in others:
            ob = ((values >> lines[o]["bit"]) & 1).astype(np.int8)
            during = ob[clk_idx]
            active = 0 if lines[o]["idle"] == 1 else 1
            frac = float(np.mean(during == active)) if clk_idx.size else 0.0
            share = float(np.mean(ob == active))
            if frac > 0.97 and share < 0.95 and lines[o]["edges"] < lines[clk]["edges"] / 2:
                cs.append(o)
        cs.sort(key=lambda o: lines[o]["edges"])
        data = [o for o in others if o not in cs]
        f_clk = 1 / (2 * lines[clk]["median_pulse_s"])
        out["clock_line"], out["clock_hz"] = clk, _r(f_clk)
        if cs and data:
            guess, conf = "SPI", "medium"
            out["chip_select"] = cs[0]
            out["data_lines"] = data
            setup = {"BUS<b>:TYPE": "SPI", "CLOCk:SOURce": clk, "CS:SOURce": cs[0],
                     "CS:POLarity": "NEG" if lines[cs[0]]["idle"] == 1 else "POS",
                     "MOSI/MISO:SOURce": data}
        elif len(data) == 1 and both_idle_high:
            guess, conf = "I2C", "medium"
            setup = {"BUS<b>:TYPE": "I2C", "CLOCk:SOURce": clk, "DATA:SOURce": data[0]}
        elif not data:
            guess, conf = f"bursts of clock on {clk}", "low"
        else:
            guess = "synchronous bus (clock + data); try SPI"
    else:
        # single asynchronous line with quantized pulses: UART
        cand = [k for k, v in lines.items() if v["quantized"] > 0.8]
        if cand:
            k = max(cand, key=lambda x: lines[x]["edges"])
            v = lines[k]
            raw = 1 / v["unit_pulse_s"]
            baud = min(_STD_BAUD, key=lambda b: abs(b - raw) / b)
            guess, conf = "UART", "medium" if abs(baud - raw) / baud < 0.03 else "low"
            out["line"], out["baud_measured"], out["baud_standard"] = k, _r(raw), baud
            out["polarity"] = "idle high (normal)" if v["idle"] == 1 else "idle low (inverted, e.g. SBUS)"
            setup = {"BUS<b>:TYPE": "UART", "DATA:SOURce": k, "BAUDrate": baud,
                     "POLarity": "IDLH" if v["idle"] == 1 else "IDLL"}
    out["guess"], out["confidence"] = guess, conf
    if setup:
        out["decoder_setup"] = setup
        out["confirm"] = "set up this decoder, capture BUSn, and check the frames decode with status OK"
    return out
