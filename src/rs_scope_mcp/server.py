"""rs-scope-mcp MCP server.

Task tools (harvest into the local store, return summaries, drill down on request):
  session      - group captures by task and goal
  capture      - acquire + harvest analog channels, logic pods and bus decodes in one call
  measure      - automatic measurements as numbers
  spectrum     - FFT with the scope's coupled settings handled, peaks returned
  bode         - Bode sweep (guarded), -3 dB points and shape returned
  history      - segmented memory: info, or harvest every segment
  data         - drill into stored series, edges and frames, or read-only SQL
  demo         - a looping show of what the scope can do (analog, or full with the logic probe)
  mask         - pass/fail tolerance masks: create from a good signal, test, count failures
  setup        - show, save or restore the instrument setup

Foundation tools (the complete SCPI command set):
  connection   - connect / status / disconnect (identity + installed options)
  scpi_search  - find commands in the registry by keyword
  scpi_describe- full description of one command (params, enums, ranges, *RST, page)
  scpi         - run one command or a batch; validated, error-checked, guarded
  scpi_block   - binary block queries (waveforms, files) saved to the data dir
  screenshot   - grab the scope screen as a PNG (returned as an image + saved)

See docs/ARCHITECTURE.md for the layers.

Config (environment): RS_SCOPE_HOST, RS_SCOPE_PORT (5025), RS_SCOPE_TIMEOUT (10),
RS_SCOPE_DATA_DIR (~/rs-scope-data).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np
from mcp.server.fastmcp import FastMCP, Image

from . import __version__
from . import analysis as an
from .instrument import GuardedCommand, Instrument, InstrumentError, ValidationError
from .registry import Registry, describe
from .scope import Scope, ScopeError
from .scope import num as scope_num
from .store import Store
from .transport import TransportError

mcp = FastMCP("rs-scope-mcp")
mcp._mcp_server.version = __version__  # otherwise clients see the mcp library version

_registry = Registry.default()
_state: dict[str, Any] = {
    "host": os.environ.get("RS_SCOPE_HOST", ""),
    "port": int(os.environ.get("RS_SCOPE_PORT", "5025") or 5025),
    "timeout": float(os.environ.get("RS_SCOPE_TIMEOUT", "10") or 10),
    "inst": None,
}
DATA_DIR = Path(os.environ.get("RS_SCOPE_DATA_DIR") or Path.home() / "rs-scope-data").expanduser()
_demo_box: dict[str, Any] = {}


def _inst(for_demo: bool = False) -> Instrument:
    d = _demo_box.get("demo")
    if d is not None and not for_demo and _demo_box["thread"].is_alive():
        raise RuntimeError("The demo is running on the scope (it allows one client at a time). "
                           "Call demo(action='stop') first.")
    inst: Instrument | None = _state["inst"]
    if inst is None:
        if not _state["host"]:
            raise RuntimeError("No scope configured. Call connection(action='connect', host='<ip>') "
                               "or set RS_SCOPE_HOST.")
        inst = Instrument(_state["host"], _state["port"], _state["timeout"], _registry)
        _state["inst"] = inst
    if not inst.connected:
        inst.connect()
    return inst


def _err(e: Exception) -> dict:
    kind = {ValidationError: "validation", GuardedCommand: "guarded", TransportError: "transport",
            InstrumentError: "instrument", ScopeError: "scope"}
    for cls, name in kind.items():
        if isinstance(e, cls):
            return {"ok": False, "error": name, "message": str(e)}
    return {"ok": False, "error": type(e).__name__, "message": str(e)}


@mcp.tool()
def connection(action: str = "status", host: str | None = None, port: int | None = None) -> dict:
    """Connect to, inspect, or disconnect from the RTB2 oscilloscope.

    action: "status" | "connect" | "disconnect".
    host/port: the scope's LAN address (port defaults to 5025, the RTB2 "IP Port").
    Returns identity (*IDN?) and installed options (*OPT?), e.g. B1 (MSO logic), B6
    (generator), K1/K2/K3 (serial decode), K36 (Bode plot).
    """
    try:
        if action == "disconnect":
            if _state["inst"]:
                _state["inst"].close()
            return {"ok": True, "connected": False}
        if action == "connect":
            if host:
                if _state["inst"]:
                    _state["inst"].close()
                _state.update(host=host, port=port or _state["port"], inst=None)
            inst = _inst()
            return {"ok": True, "connected": True, "host": _state["host"], "port": _state["port"],
                    "idn": inst.idn, "options": inst.options}
        inst: Instrument | None = _state["inst"]
        return {
            "ok": True,
            "connected": bool(inst and inst.connected),
            "host": _state["host"] or None,
            "port": _state["port"],
            "idn": inst.idn if inst else None,
            "options": inst.options if inst else None,
            "registry": _registry.meta,
            "data_dir": str(DATA_DIR),
        }
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool()
def scpi_search(query: str, limit: int = 15) -> dict:
    """Search the complete RTB2 SCPI command registry (~950 documented headers).

    Use plain words ("i2c frame data", "trigger edge slope", "bode gain") and get the
    matching command headers with kind (query/set/set_query/event), summary and manual page.
    """
    hits = _registry.search(query, limit=limit)
    return {"count": len(hits), "commands": [
        {"header": c.header, "kind": c.kind, "summary": c.summary[:160], "page": c.page} for c in hits]}


@mcp.tool()
def scpi_describe(header: str, verbose: bool = False) -> dict:
    """Describe one SCPI command: suffix ranges, parameters, enum values, ranges, *RST
    default, unit, return values and manual page. Accepts the manual header
    ("CHANnel<m>:SCALe") or a concrete command ("chan2:scal?")."""
    c = _registry.by_header(header) or _registry.by_header(header.split()[0])
    if not c:
        return {"ok": False, "message": f"no command matches {header!r}; try scpi_search"}
    return {"ok": True, **describe(c, verbose=verbose)}


@mcp.tool()
def scpi(commands: str | list[str], confirm: bool = False, wait: bool = False) -> dict:
    """Send SCPI to the scope. Queries (ending in '?') return the response.

    commands: one command, or a list. Consecutive queries in a list are batched into
      as few round trips as possible.
    confirm: required for guarded commands (generator/pattern outputs, Bode sweeps,
      *RST, self-alignment, file deletion, network settings, firmware).
    wait: after a write, block until the scope finishes (*OPC?), e.g. for SINGle.
    Every command is checked against the registry first (header, suffix range, values)
    and the scope's error queue is read after each write, so failures are reported.
    For binary data (waveforms, screenshots, files) use scpi_block / screenshot.
    """
    try:
        inst = _inst()
        lines = [commands] if isinstance(commands, str) else list(commands)
        lines = [ln.strip() for ln in lines if ln and ln.strip()]
        # validate and guard-check the whole batch before anything is sent
        guarded = []
        for ln in lines:
            r = inst.check(ln)
            reason = inst.guard_reason(r, ln)
            if reason and not confirm:
                guarded.append(f"{ln!r}: {reason}")
        if guarded:
            return {"ok": False, "error": "guarded", "sent": [],
                    "message": "Nothing was sent. Guarded commands need confirm=true: " + "; ".join(guarded)}
        results: list[dict] = []
        pending: list[str] = []

        def flush():
            if not pending:
                return
            t0 = time.monotonic()
            vals = inst.query_many(pending)
            ms = (time.monotonic() - t0) * 1000 / len(pending)
            for k, (ln, v) in enumerate(zip(pending, vals)):
                if v is None:
                    results.append({"sent": ln, "ok": False,
                                    "instrument_errors": inst.last_errors.get(k, [])})
                else:
                    results.append({"sent": ln, "ok": True, "response": v, "elapsed_ms": round(ms, 1)})
            pending.clear()

        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            if ln.split()[0].endswith("?"):
                pending.append(ln)
                continue
            flush()
            results.append(inst.write(ln, confirm=confirm, wait=wait).as_dict())
        flush()
        ok = all(r.get("ok", True) for r in results)
        return {"ok": ok, "results": results}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool()
def scpi_block(query: str, save_as: str | None = None, timeout: float = 30.0) -> dict:
    """Run a query that returns IEEE 488.2 binary block data and save it under the data dir.

    Examples: "CHAN1:DATA?" (after FORM REAL,32), "DIG0:DATA?", "MMEM:DATA? 'file.csv'",
    "HCOP:DATA?". Returns the saved path and size; the bytes are not put into the chat.
    """
    try:
        data = _inst().query_block(query, timeout=timeout)
        DATA_DIR.joinpath("raw").mkdir(parents=True, exist_ok=True)
        name = save_as or f"{time.strftime('%Y%m%d-%H%M%S')}-{query.split()[0].strip(':?*').replace(':', '_')}.bin"
        path = DATA_DIR / "raw" / Path(name).name
        path.write_bytes(data)
        return {"ok": True, "path": str(path), "bytes": len(data)}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool()
def screenshot(note: str | None = None) -> list:
    """Capture the scope screen. Returns the PNG as an image so you can see the display,
    and saves it under <data dir>/screenshots with a JSON sidecar (time, note, settings)."""
    try:
        inst = _inst()
        inst.write("HCOPy:LANGuage PNG", check_errors=False)
        png = inst.query_block("HCOPy:DATA?", timeout=30)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        folder = DATA_DIR / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{stamp}.png"
        path.write_bytes(png)
        meta = {"time": stamp, "note": note, "idn": inst.idn.get("raw"), "path": str(path)}
        path.with_suffix(".json").write_text(json.dumps(meta, indent=1))
        return [Image(data=png, format="png"), json.dumps({"ok": True, **meta})]
    except Exception as e:  # noqa: BLE001
        return [json.dumps(_err(e))]


# =====================================================================================
# Task-level tools: harvest into the local store, return summaries, drill down on request
# =====================================================================================
_store_box: dict[str, Store] = {}


def _store() -> Store:
    if "s" not in _store_box:
        _store_box["s"] = Store(DATA_DIR)
    return _store_box["s"]


def _scope() -> Scope:
    return Scope(_inst())


def _parse_source(src: str) -> tuple[str, int]:
    s = src.strip().upper().replace(" ", "")
    if s in ("CH1", "CH2", "CH3", "CH4"):
        return "analog", int(s[2])
    if s in ("POD1", "LOGIC", "LOGIC1", "D0-D7", "D0..D7"):
        return "pod", 1
    if s in ("POD2", "LOGIC2", "D8-D15", "D8..D15"):
        return "pod", 2
    if s in ("BUS1", "B1", "BUS2", "B2"):
        return "bus", int(s[-1])
    raise ValueError(f"unknown source {src!r}: use CH1..CH4, POD1 (D0-D7), POD2 (D8-D15), BUS1, BUS2")


_BRIEF = ("timebase_s_div", "sample_rate", "record_length", "trigger_source", "trigger_type", "trigger_mode",
          "mode")


def _harvest(scope: Scope, store: Store, cid: str, sources: list[str], points: str,
             segment: int | None = None) -> dict:
    out: dict = {}
    for src in sources:
        try:
            kind, n = _parse_source(src)
            if kind == "analog":
                s = scope.analog(n, points)
                stats = an.analog_summary(s.values, s.dt, s.meta.get("codes_max"))
                store.add_series(cid, s.source, s.kind, s.t0, s.dt, s.unit, s.values, stats)
                out[s.source] = stats
            elif kind == "pod":
                s = scope.logic_pod(n, points)
                stats = an.pod_edges(s.values, s.t0, s.dt, s.meta["first_channel"])
                store.add_series(cid, s.source, s.kind, s.t0, s.dt, s.unit, s.values, stats)
                out[s.source] = stats
            else:
                dec = scope.decode(n)
                store.add_frames(cid, dec, segment)
                out[f"BUS{n}"] = an.frames_summary(dec)
        except Exception as e:  # noqa: BLE001 - one bad source must not lose the others
            out[src] = {"error": str(e)}
    return out


@mcp.tool()
def session(action: str = "current", goal: str | None = None, limit: int = 20) -> dict:
    """Group harvested data by task. action: "start" (with a goal, e.g. "rev C bring-up, 3V3 ripple"),
    "current", or "list". Every capture is stored under the current session."""
    try:
        st = _store()
        if action == "start":
            return {"ok": True, "session": st.start_session(goal or "")}
        if action == "list":
            return {"ok": True, "sessions": st.sessions(limit)}
        sid = st.session()
        return {"ok": True, "session": sid, "captures": st.captures(limit=limit, session=sid),
                "data_dir": str(DATA_DIR)}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool()
def capture(sources: list[str] | None = None, acquire: str = "single", points: str = "MAX",
            note: str | None = None, screenshot: bool = False, include_setup: bool = True) -> dict:
    """Acquire and harvest in one call; data goes to the local store, a summary comes back.

    sources: any of CH1..CH4 (volts), POD1 (D0-D7) / POD2 (D8-D15) logic, BUS1 / BUS2 decoded frames.
    acquire: "single" (SINGle and wait, default), "stop" (stop and keep what is on screen), or
      "current" (read the running acquisition; screen points only).
    points: "MAX" (full memory, needs a stopped acquisition), "DMAX", or "DEF" (screen).
    Returns a capture id plus per-source summaries (levels, frequency, edges, frame counts and
    statuses, addresses, ASCII preview). Use `data` to drill into samples, edges or frames.
    """
    try:
        sc, st = _scope(), _store()
        sources = sources or ["CH1"]
        kinds = {_parse_source(s)[0] for s in sources}
        # Bode, XY and mask applications block logic and decode settings (-221)
        if kinds & {"bus", "pod"} or sc.mode() in ("BPL", "XY"):
            sc.ensure_yt()
        # decoding must be on before the acquisition, or that record is not decoded
        enabled = []
        for src in sources:
            k, n = _parse_source(src)
            if k == "bus" and sc.q(f"BUS{n}:STATe?").strip() not in ("1", "ON"):
                sc.w(f"BUS{n}:STATe ON")
                enabled.append(f"BUS{n}")
        t_acq = None
        if acquire == "single":
            t_acq = round(sc.single(), 3)
        elif acquire == "stop":
            sc.stop()
        elif acquire == "current":
            points = "DEF"
        settings = sc.settings()
        setup_blob = sc.snapshot() if include_setup else None
        cid = st.new_capture("capture", note, _inst().idn.get("raw"), settings, setup_blob)
        results = _harvest(sc, st, cid, sources, points)
        shot = None
        if screenshot:
            shot = str(st.add_artifact(cid, "screenshot", "screen.png", sc.screenshot()))
        summary = {"capture": cid, "acquire_s": t_acq, "settings": {k: settings.get(k) for k in _BRIEF},
                   "sources": results}
        if shot:
            summary["screenshot"] = shot
        if enabled:
            summary["note"] = f"decoding was off on {', '.join(enabled)}; switched it on before acquiring"
        st.set_summary(cid, summary)
        return {"ok": True, **summary,
                "next": "data(action='series'|'edges'|'frames', capture_id=...) for details"}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool()
def measure(source: str = "CH1", types: list[str] | None = None, settle_s: float = 1.0) -> dict:
    """Automatic measurements on a source (up to 8): e.g. FREQ, PER, PEAK, UPE, LPE, AMPL, MEAN, RMS,
    RTIM, FTIM, PDCY, NDCY, PPW, NPW, POV, NOV, DEL, PHAS. Values come back as numbers (None when the
    scope has nothing to measure) and are stored with a capture record."""
    try:
        sc, st = _scope(), _store()
        vals = sc.measure(source, types, settle_s)
        cid = st.new_capture("measure", None, _inst().idn.get("raw"), sc.settings())
        st.add_measurements(cid, source, vals)
        st.set_summary(cid, {"source": source, "values": vals})
        return {"ok": True, "capture": cid, "source": source, "values": vals}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool()
def spectrum(source: str = "CH1", start_hz: float = 0.0, stop_hz: float = 1e6, window: str = "HANN",
             note: str | None = None) -> dict:
    """FFT of a channel between start_hz and stop_hz. Handles the scope's coupled span/RBW/timebase
    settings, stores the spectrum, and returns the noise floor and the strongest peaks."""
    try:
        sc, st = _scope(), _store()
        s = sc.spectrum(source, start_hz, stop_hz, window)
        peaks = an.spectrum_peaks(s.t0, s.dt, s.values)
        peaks["rbw_hz"] = s.meta.get("rbw_hz")
        cid = st.new_capture("spectrum", note, _inst().idn.get("raw"), sc.settings())
        st.add_series(cid, s.source, s.kind, s.t0, s.dt, s.unit, s.values, peaks)
        st.set_summary(cid, peaks)
        return {"ok": True, "capture": cid, **peaks}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool()
def bode(start_hz: float, stop_hz: float, points_per_decade: int = 20, input_channel: str = "CH1",
         output_channel: str = "CH2", confirm: bool = False, note: str | None = None) -> dict:
    """Bode plot (option K36): sweeps the generator into the DUT and measures gain and phase.
    Guarded: it drives the generator output, so it needs confirm=true. Returns -3 dB / -6 dB points,
    peak, low/high-frequency gain, phase there and the response shape; arrays are stored."""
    try:
        import io

        import numpy as np
        sc, st = _scope(), _store()
        r = sc.bode(start_hz, stop_hz, points_per_decade, input_channel, output_channel, confirm=confirm)
        summ = an.bode_summary(r["freq_hz"], r["gain_db"], r["phase_deg"])
        summ.update(seconds=r["seconds"], input=r["input"], output=r["output"], generator_load=r["load"])
        cid = st.new_capture("bode", note, _inst().idn.get("raw"), sc.settings())
        buf = io.BytesIO()
        np.savez(buf, freq_hz=r["freq_hz"], gain_db=r["gain_db"], phase_deg=r["phase_deg"])
        st.add_artifact(cid, "bode", "bode.npz", buf.getvalue(), summ)
        st.set_summary(cid, summ)
        return {"ok": True, "capture": cid, **summ}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool()
def history(action: str = "info", sources: list[str] | None = None, max_segments: int = 50,
            note: str | None = None) -> dict:
    """Segmented memory (K15). action "info": segments available, index range, relative times.
    action "harvest": walk the segments (newest first, up to max_segments) and store each one's
    sources (CH*, POD*, BUS*) as its own capture; returns one compact row per segment so faults
    can be spotted, e.g. a segment with non-OK frames."""
    try:
        sc, st = _scope(), _store()
        info = sc.history()
        if action == "info":
            rel = info.pop("relative_times")
            return {"ok": True, **info, "relative_times_s": rel[:20], "more_times": max(0, len(rel) - 20)}
        sources = sources or ["CH1"]
        if any(_parse_source(s)[0] == "bus" for s in sources):
            sc.ensure_yt()
        rows, seen = [], set()
        settings = sc.settings()
        for k in range(info["last"], info["first"] - 1, -1)[:max_segments]:
            real = sc.select_segment(k)
            if real in seen:
                continue
            seen.add(real)
            cid = st.new_capture("segment", note, _inst().idn.get("raw"), settings, None, segment=real)
            res = _harvest(sc, st, cid, sources, "DEF", segment=real)
            row: dict = {"segment": real, "capture": cid}
            for name, s in res.items():
                if "error" in s:
                    row[name] = s["error"]
                elif "frames" in s:
                    row[name] = {"frames": s["frames"], "status": s.get("status"),
                                 **({"non_ok": s["non_ok_frames"]} if s.get("non_ok_frames") else {})}
                elif "vpp" in s:
                    row[name] = {k2: s.get(k2) for k2 in ("vpp", "rms_v", "frequency_hz") if s.get(k2) is not None}
                else:
                    row[name] = {"active": list(s.get("active", {}))}
            rows.append(row)
        return {"ok": True, "available": info["available"], "harvested": len(rows), "segments": rows}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool()
def data(action: str = "captures", capture_id: str | None = None, source: str | None = None,
         t_start: float | None = None, t_end: float | None = None, max_points: int = 400,
         bus: int | None = None, status: str | None = None, address: int | None = None,
         contains: str | None = None, segment: int | None = None, offset: int = 0, limit: int = 50,
         sql: str | None = None) -> dict:
    """Drill into the local store. Nothing here touches the scope.

    action:
      "sessions" | "captures" (recent, current session) | "capture" (one capture: settings, summaries)
      "series": a window [t_start, t_end] of a stored series, min/max-decimated to max_points rows
      "edges": edge times per logic channel in a window (source e.g. "D0-D7")
      "envelope": envelope of a stored analog series (source "CH1"): AM depth, 10-90 % rise and
                  fall of keyed carriers, crest factor
      "identify": guess what a stored logic pod carries (I2C, SPI, UART with baud, PWM/servo, clock)
                  and the decoder setup to confirm it
      "frames": decoded frames, filtered by capture_id, bus, status ("not_ok" for errors), address,
                contains (text or hex), segment, time window; paged with offset/limit
      "sql": a read-only SELECT over the catalog (tables: sessions, captures, series, frames,
             measurements, artifacts)
    """
    try:
        st = _store()
        if action == "sessions":
            return {"ok": True, "sessions": st.sessions(limit)}
        if action == "captures":
            return {"ok": True, "captures": st.captures(limit=limit, session=st.session())}
        if action == "capture":
            c = st.capture(capture_id or "")
            return {"ok": bool(c), **(c or {"message": "no such capture"})}
        if action == "series":
            meta, arr = st.series(capture_id or "", source or "")
            return {"ok": True, "source": source, "unit": meta["unit"], "n": meta["n"], "dt": meta["dt"],
                    **an.minmax_decimate(arr, meta["t0"], meta["dt"], t_start, t_end, max_points)}
        if action == "edges":
            meta, arr = st.series(capture_id or "", source or "D0-D7")
            i0 = 0 if t_start is None else max(0, int((t_start - meta["t0"]) / meta["dt"]))
            i1 = meta["n"] if t_end is None else min(meta["n"], int((t_end - meta["t0"]) / meta["dt"]) + 1)
            first = int(meta["stats"].get("first_channel", 0)) if isinstance(meta["stats"], dict) else 0
            first = 8 if (source or "").upper().startswith("D8") else first
            return {"ok": True, **an.pod_edges(arr[i0:i1], meta["t0"] + i0 * meta["dt"], meta["dt"], first,
                                               max_edges=min(limit, 200))}
        if action == "envelope":
            meta, arr = st.series(capture_id or "", source or "CH1")
            i0 = 0 if t_start is None else max(0, int((t_start - meta["t0"]) / meta["dt"]))
            i1 = meta["n"] if t_end is None else min(meta["n"], int((t_end - meta["t0"]) / meta["dt"]) + 1)
            return {"ok": True, "source": source or "CH1", **an.envelope(np.asarray(arr[i0:i1]), meta["dt"])}
        if action == "identify":
            meta, arr = st.series(capture_id or "", source or "D0-D7")
            first = 8 if (source or "").upper().startswith("D8") else 0
            return {"ok": True, "source": source or "D0-D7",
                    **an.identify_bus(np.asarray(arr), meta["t0"], meta["dt"], first)}
        if action == "frames":
            return {"ok": True, **st.frames(capture_id, bus, status, address, contains, t_start, t_end, segment,
                                            offset, limit)}
        if action == "sql":
            return {"ok": True, **st.sql(sql or "", limit)}
        return {"ok": False, "message": f"unknown action {action!r}"}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool()
def mask(action: str = "status", source: str = "CH1", x_margin_div: float = 0.1, y_margin_div: float = 0.2,
         acquisitions: int = 1000, timeout_s: float = 120.0, keep_failed: bool = True,
         stop_on_failures: int = 0, note: str | None = None) -> dict:
    """Pass/fail mask testing: does the signal stay inside a tolerance envelope?

    action "create": learn a mask from the signal now on `source` (its envelope) widened by
      x_margin_div / y_margin_div divisions. Show the known-good signal first.
    action "test": run until `acquisitions` have been tested (or timeout_s); returns tested, failed,
      failure rate and rate per second. keep_failed stores only failing acquisitions in segmented
      memory for history(action="harvest"); stop_on_failures stops at that many violations.
    action "status": current counts. action "off": end the mask test.
    """
    try:
        sc, st = _scope(), _store()
        if action == "create":
            r = sc.mask_create(source, x_margin_div, y_margin_div)
            return {"ok": "warning" not in r, **r}
        if action == "test":
            r = sc.mask_test(acquisitions, timeout_s, keep_failed, stop_on_failures)
            cid = st.new_capture("mask", note, _inst().idn.get("raw"), sc.settings())
            st.set_summary(cid, r)
            return {"ok": True, "capture": cid, **r}
        if action == "status":
            tested, failed = (int(an_num or 0) for an_num in
                              (scope_num(v) for v in sc.qm(["MASK:COUNt?", "MASK:VCOunt?"])))
            return {"ok": True, "mask_on": sc.q("MASK:STATe?").strip() in ("1", "ON"), "tested": tested,
                    "failed": failed}
        if action == "off":
            sc.mask_off()
            return {"ok": True}
        return {"ok": False, "message": f"unknown action {action!r}"}
    except Exception as e:  # noqa: BLE001
        return _err(e)


DEMO_WIRING = {
    "analog": "Aux Out -> 50 ohm feed-through terminator -> CH1. Nothing else.",
    "full": "Aux Out -> 50 ohm feed-through terminator -> CH1, and pattern generator P0..P3 -> logic probe "
            "D0..D3 (probe ground to the pattern generator's ground contact).",
}


@mcp.tool()
def demo(action: str = "status", level: str = "analog", dwell_s: float = 12.0, loops: int = 0,
         confirm: bool = False) -> dict:
    """Demo mode: the scope plays a looping show of what it can do (heartbeat monitor, glowing
    noisy signal, radar ping, AM, FM spectrum, harmonics, bursts, a pass/fail mask test, and with the
    logic probe wired, live I2C/SPI decoding and a logic-analyzer counter).

    action: "scenes" (list them and the wiring), "start", "stop", "status".
    level: "analog" (Aux Out -> 50 ohm terminator -> CH1) or "full" (plus pattern generator P0..P3 ->
      logic probe D0..D3). dwell_s: seconds per scene. loops: 0 = until stopped.
    Starting is guarded (confirm=true): it drives the generator and pattern-generator outputs.
    The setup is snapshotted first and restored when the demo stops. While it runs, other tools wait
    (the scope accepts one LAN client at a time).
    """
    import threading

    from .demo import Demo, scenes_for
    try:
        d = _demo_box.get("demo")
        running = bool(d is not None and _demo_box["thread"].is_alive())
        if action == "scenes":
            return {"ok": True, "level": level, "wiring": DEMO_WIRING.get(level),
                    "scenes": [{"id": x.id, "title": x.title, "caption": x.caption, "level": x.level}
                               for x in scenes_for(level)]}
        if action == "status":
            out = {"ok": True, "running": running}
            if d is not None:
                out.update(current=d.current, log=_demo_box.get("log", [])[-5:],
                           result=_demo_box.get("result"), level=d.level)
            return out
        if action == "stop":
            if not running:
                return {"ok": True, "running": False, "result": _demo_box.get("result")}
            d.stop()
            _demo_box["thread"].join(timeout=60)
            return {"ok": True, "running": _demo_box["thread"].is_alive(), "result": _demo_box.get("result")}
        if action == "start":
            if running:
                return {"ok": False, "message": "the demo is already running"}
            if not confirm:
                return {"ok": False, "error": "guarded",
                        "message": "the demo drives the generator (and in 'full' the pattern generator); "
                                   f"wiring: {DEMO_WIRING.get(level)} Resend with confirm=true."}
            log: list[str] = []
            inst = _inst(for_demo=True)
            dm = Demo(inst, level=level, dwell=dwell_s, loops=loops, log=log.append)

            def runner():
                try:
                    _demo_box["result"] = dm.run()
                except Exception as e:  # noqa: BLE001
                    _demo_box["result"] = {"error": str(e)}

            th = threading.Thread(target=runner, name="rs-scope-demo", daemon=True)
            _demo_box.update(demo=dm, thread=th, log=log, result=None)
            th.start()
            return {"ok": True, "running": True, "level": level, "scenes": [x.id for x in dm.scenes],
                    "dwell_s": dwell_s, "wiring": DEMO_WIRING[level], "stop_with": "demo(action='stop')"}
        return {"ok": False, "message": f"unknown action {action!r}"}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool()
def setup(action: str = "show", capture_id: str | None = None, note: str | None = None,
          confirm: bool = False) -> dict:
    """The scope's setup. "show": the settings that matter (channels, timebase, trigger, buses,
    generators). "save": store the complete setup (SYSTem:SET) as a capture to come back to.
    "restore": load the setup stored with capture_id; guarded (confirm=true) because it replaces
    every setting and can re-enable outputs that were on when it was saved."""
    try:
        sc, st = _scope(), _store()
        if action == "show":
            return {"ok": True, "settings": sc.settings()}
        if action == "save":
            cid = st.new_capture("setup", note, _inst().idn.get("raw"), sc.settings(), sc.snapshot())
            return {"ok": True, "capture": cid}
        if action == "restore":
            if not confirm:
                return {"ok": False, "error": "guarded",
                        "message": "restore replaces every setting and can re-enable outputs; resend with confirm=true"}
            blob = (st.dir(capture_id or "") / "setup.set").read_bytes()
            errs = sc.restore(blob)
            return {"ok": not errs, "restored_from": capture_id, "instrument_errors": errs}
        return {"ok": False, "message": f"unknown action {action!r}"}
    except Exception as e:  # noqa: BLE001
        return _err(e)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
