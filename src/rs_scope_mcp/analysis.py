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
