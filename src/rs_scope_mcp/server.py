"""rs-scope-mcp MCP server — foundation tools.

This first cut exposes the complete SCPI foundation:
  connection   - connect / status / disconnect (identity + installed options)
  scpi_search  - find commands in the registry by keyword
  scpi_describe- full description of one command (params, enums, ranges, *RST, page)
  scpi         - run one command or a batch; validated, error-checked, guarded
  scpi_block   - binary block queries (waveforms, files) saved to the data dir
  screenshot   - grab the scope screen as a PNG (returned as an image + saved)

Typed, task-level tools (capture, decode, logic, bode, history, measurements) and the
local data store build on these in the next layers (docs/ARCHITECTURE.md).

Config (environment): RS_SCOPE_HOST, RS_SCOPE_PORT (5025), RS_SCOPE_TIMEOUT (10),
RS_SCOPE_DATA_DIR (~/rs-scope-data).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP, Image

from .instrument import GuardedCommand, Instrument, InstrumentError, ValidationError
from .registry import Registry, describe
from .transport import TransportError

mcp = FastMCP("rs-scope-mcp")

_registry = Registry.default()
_state: dict[str, Any] = {
    "host": os.environ.get("RS_SCOPE_HOST", ""),
    "port": int(os.environ.get("RS_SCOPE_PORT", "5025") or 5025),
    "timeout": float(os.environ.get("RS_SCOPE_TIMEOUT", "10") or 10),
    "inst": None,
}
DATA_DIR = Path(os.environ.get("RS_SCOPE_DATA_DIR") or Path.home() / "rs-scope-data").expanduser()


def _inst() -> Instrument:
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
            InstrumentError: "instrument"}
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
