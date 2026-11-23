"""`rs-scope` command line: bench checks without an AI in the loop.

  rs-scope --host 192.168.1.50 idn
  rs-scope --host 192.168.1.50 query "CHAN1:SCAL?"
  rs-scope --host 192.168.1.50 write "TIM:SCAL 1e-3"
  rs-scope --host 192.168.1.50 screenshot shot.png
  rs-scope --host 192.168.1.50 tree-diff
  rs-scope search "i2c frame"
  rs-scope describe "BUS<b>:I2C:FRAMe<n>:DATA?"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from .instrument import GuardedCommand, Instrument, InstrumentError, ValidationError
from .registry import Registry, describe
from .transport import TransportError


def _inst(a) -> Instrument:
    if not a.host:
        sys.exit("error: --host (or RS_SCOPE_HOST) is required for this command")
    i = Instrument(a.host, a.port, timeout=a.timeout, validate=not a.no_validate)
    i.connect()
    return i


def cmd_tree_diff(a) -> int:
    """Compare the registry with the firmware's own command list (SYSTem:TREE?)."""
    i = _inst(a)
    t0 = time.monotonic()
    tree = i.transport.query("SYSTem:TREE?", timeout=90)  # ~60 kB, ~16 s on FW 03.000
    fw = sorted({h.strip() for h in re.split(r"[;\n]", tree) if h.strip() and not h.strip().startswith("&")})
    reg = i.registry
    manual_only = [h for h in fw if not any(c.source == "manual" for c, _, _ in reg.match_header(h))]
    unknown = [h for h in fw if not reg.match_header(h)]
    out = {
        "firmware": i.idn.get("firmware"),
        "model": i.idn.get("model"),
        "firmware_headers": len(fw),
        "documented_in_manual": len(fw) - len(manual_only),
        "undocumented": len(manual_only),
        "unknown_to_registry": unknown[:200],
        "seconds": round(time.monotonic() - t0, 1),
    }
    if a.save:
        name = f"{i.idn.get('model', 'RTB2')}-{i.idn.get('firmware', 'unknown')}.tree"
        path = Path(a.save) / name
        path.write_text("\n".join(fw) + "\n")
        out["saved"] = str(path)
    print(json.dumps(out, indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rs-scope", description="R&S RTB2 bench CLI (rs-scope-mcp)")
    ap.add_argument("--host", default=os.environ.get("RS_SCOPE_HOST"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("RS_SCOPE_PORT", "5025")))
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--no-validate", action="store_true", help="send commands unknown to the registry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("idn")
    q = sub.add_parser("query")
    q.add_argument("line", nargs="+")
    w = sub.add_parser("write")
    w.add_argument("line")
    w.add_argument("--confirm", action="store_true")
    s = sub.add_parser("screenshot")
    s.add_argument("path", nargs="?", default="rtb2-screenshot.png")
    td = sub.add_parser("tree-diff", help="compare the registry with the firmware's SYSTem:TREE?")
    td.add_argument("--save", metavar="DIR", help="save the tree for src/rs_scope_mcp/data/rtb2/firmware/")
    se = sub.add_parser("search")
    se.add_argument("terms", nargs="+")
    d = sub.add_parser("describe")
    d.add_argument("header")
    sub.add_parser("stats", help="registry statistics")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "idn":
            print(json.dumps(_inst(a).connect(), indent=1))
        elif a.cmd == "query":
            i = _inst(a)
            if len(a.line) == 1:
                print(json.dumps(i.query(a.line[0], check_errors=True).as_dict(), indent=1))
            else:
                print(json.dumps(dict(zip(a.line, i.query_many(a.line))), indent=1))
        elif a.cmd == "write":
            print(json.dumps(_inst(a).write(a.line, confirm=a.confirm).as_dict(), indent=1))
        elif a.cmd == "screenshot":
            i = _inst(a)
            i.write("HCOPy:LANGuage PNG")
            data = i.query_block("HCOPy:DATA?", timeout=30)
            with open(a.path, "wb") as f:
                f.write(data)
            print(f"wrote {a.path} ({len(data)} bytes)")
        elif a.cmd == "tree-diff":
            return cmd_tree_diff(a)
        elif a.cmd == "search":
            reg = Registry.default()
            for c in reg.search(" ".join(a.terms)):
                print(f"{c.header:55s} {c.kind:9s} p{c.page:<4d} {c.summary[:70]}")
        elif a.cmd == "describe":
            reg = Registry.default()
            c = reg.by_header(a.header)
            if not c:
                print("not found", file=sys.stderr)
                return 1
            print(json.dumps(describe(c, verbose=True), indent=1, ensure_ascii=False))
        elif a.cmd == "stats":
            reg = Registry.default()
            print(json.dumps({"meta": reg.meta, "subsystems": reg.subsystems()}, indent=1))
    except (ValidationError, GuardedCommand, InstrumentError, TransportError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
