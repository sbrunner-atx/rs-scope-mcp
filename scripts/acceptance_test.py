"""Acceptance test on real hardware: drive the MCP server over stdio, as Claude Desktop does.

Wiring: Aux Out -> 50 ohm terminator -> CH1; pattern generator P0..P3 -> logic probe D0..D3.
    RS_SCOPE_HOST=<ip> uv run python scripts/acceptance_test.py [results.json]
    RS_SCOPE_EXT_DIR=<installed extension folder> ... to test an installed .mcpb
The setup is saved first and restored at the end."""
import asyncio

# ruff: noqa: SIM117, SIM115
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXT = os.environ.get("RS_SCOPE_EXT_DIR", str(__import__("pathlib").Path(__file__).resolve().parents[1]))
ENV = dict(os.environ, RS_SCOPE_HOST=os.environ.get("RS_SCOPE_HOST", ""), RS_SCOPE_PORT=os.environ.get("RS_SCOPE_PORT", "5025"),
           RS_SCOPE_DATA_DIR=os.environ.get("RS_SCOPE_DATA_DIR", ""))
results = []


def check(step, name, ok, detail):
    results.append({"step": step, "check": name, "pass": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {step} {name}: {detail}", flush=True)


async def main():
    params = StdioServerParameters(command="uv", args=["run", "--directory", EXT, "rs-scope-mcp"], env=ENV)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(tool, **kw):
                res = await s.call_tool(tool, kw)
                texts = [c.text for c in res.content if c.type == "text"]
                images = [c for c in res.content if c.type == "image"]
                try:
                    data = json.loads(texts[-1]) if texts else {}
                except json.JSONDecodeError:
                    data = {"raw": texts}
                if images:
                    data["_images"] = len(images)
                return data

            tools = (await s.list_tools()).tools
            check("0", "tools listed", len(tools) == 16, f"{len(tools)} tools")

            # 1. basics
            c = await call("connection", action="connect")
            opts = c.get("options", [])
            check("1", "connect + options", c.get("ok") and {"B1", "B6", "K1", "K36"} <= set(opts),
                  f"{c.get('idn', {}).get('model')} FW {c.get('idn', {}).get('firmware')} options {opts}")
            await call("session", action="start", goal="acceptance test of the installed extension")
            base = await call("setup", action="save", note="before acceptance test")
            base_id = base.get("capture")
            check("1", "setup saved", base.get("ok") and base_id, base_id)
            try:
                # fixture: put I2C traffic on the logic probe (the test operator's wiring)
                await call("scpi", commands=["DEV:MODE YT", "LOG1:STAT ON", "LOG1:THR USER", "LOG1:THR:UDL 0.5",
                                             "PGEN:FUNC I2C", "PGEN:PATT:STAT ON"], confirm=True)

                # 2. unknown bus, the bus-reverse-engineering way
                await call("scpi", commands=["CHAN1:STAT OFF", "TIM:SCAL 1e-4", "TIM:REF 8.33", "TRIG:A:SOUR D0",
                                             "TRIG:A:TYPE EDGE", "TRIG:A:EDGE:SLOP NEG", "TRIG:A:MODE NORM"])
                cap = await call("capture", sources=["POD1"], note="survey")
                active = list(cap.get("sources", {}).get("D0-D7", {}).get("active", {}))
                check("2", "survey finds active lines", active == ["D0", "D1"], f"active {active}")
                ident = await call("data", action="identify", capture_id=cap.get("capture"), source="D0-D7")
                setup = ident.get("decoder_setup") or {}
                check("2", "identify", ident.get("guess") == "I2C", f"{ident.get('guess')} ({ident.get('confidence')}), "
                      f"clock {ident.get('clock_hz')} Hz, setup {setup}")
                await call("scpi", commands=["BUS1:TYPE I2C", f"BUS1:I2C:CLOC:SOUR {setup.get('CLOCk:SOURce', 'D0')}",
                                             f"BUS1:I2C:DATA:SOUR {setup.get('DATA:SOURce', 'D1')}", "BUS1:STAT ON",
                                             "BUS1:FORM ASC"])
                dec = await call("capture", sources=["BUS1"], note="confirm I2C")
                b = dec.get("sources", {}).get("BUS1", {})
                check("2", "decode confirms", b.get("status") and set(b["status"]) == {"OK"} and b.get("frames", 0) >= 2,
                      f"{b.get('frames')} frames {b.get('status')} addresses {b.get('addresses')} ascii {b.get('ascii')!r}")
                fr = await call("data", action="frames", capture_id=dec.get("capture"), bus=1, limit=5)
                texts = [row[-1] for row in fr.get("rows", [])]
                check("2", "frames readable from store", "*idn?" in texts and any("Rohde&Schwarz" in t for t in texts), texts)

                # 3. measurement: 1 kHz, 1 Vpp sine into the 50 ohm terminator
                g = await call("scpi", commands=["PGEN:PATT:STAT OFF", "LOG1:STAT OFF", "BUS1:STAT OFF", "CHAN1:STAT ON",
                                                 "WGEN:OUTP:LOAD R50", "WGEN:FUNC SIN", "WGEN:FREQ 1000", "WGEN:VOLT 1",
                                                 "WGEN:VOLT:OFFS 0", "WGEN:MOD OFF", "WGEN:OUTP ON"])
                check("3", "generator on is guarded", g.get("error") == "guarded" and not g.get("sent"), g.get("message", "")[:90])
                await call("scpi", commands=["PGEN:PATT:STAT OFF", "LOG1:STAT OFF", "BUS1:STAT OFF", "CHAN1:STAT ON",
                                             "CHAN1:SCAL 0.2", "CHAN1:POS 0", "WGEN:OUTP:LOAD R50", "WGEN:FUNC SIN",
                                             "WGEN:FREQ 1000", "WGEN:VOLT 1", "WGEN:VOLT:OFFS 0", "WGEN:MOD OFF",
                                             "TIM:SCAL 5e-4", "TIM:REF 50", "TRIG:A:SOUR CH1", "TRIG:A:EDGE:SLOP POS",
                                             "TRIG:A:LEV1 0", "TRIG:A:MODE AUTO", "WGEN:OUTP ON"], confirm=True)
                await asyncio.sleep(1)
                m = await call("measure", source="CH1", types=["FREQ", "PEAK", "RMS"])
                v = m.get("values", {})
                ok = v.get("FREQ") and abs(v["FREQ"] - 1000) < 5 and abs((v.get("PEAK") or 0) - 1.0) < 0.06 \
                    and abs((v.get("RMS") or 0) - 0.3536) < 0.02
                check("3", "1 kHz / 1 Vpp / 0.354 Vrms", ok, v)
                cap = await call("capture", sources=["CH1"], note="sine")
                s1 = cap.get("sources", {}).get("CH1", {})
                check("3", "capture summary agrees", abs((s1.get("frequency_hz") or 0) - 1000) < 5 and abs((s1.get("vpp") or 0) - 1.03) < 0.08,
                      {k: s1.get(k) for k in ("n", "frequency_hz", "vpp", "rms_v")})

                # 4. AM depth
                await call("scpi", commands=["WGEN:FREQ 100000", "WGEN:MOD:TYPE AM", "WGEN:MOD:FUNC SIN",
                                             "WGEN:MOD:AM:FREQ 1000", "WGEN:MOD:AM:DEPT 80", "WGEN:MOD ON", "TIM:SCAL 1e-3"])
                cap = await call("capture", sources=["CH1"], note="AM 80 %")
                env = await call("data", action="envelope", capture_id=cap.get("capture"), source="CH1")
                check("4", "AM depth about 75 % (set 80 %)", env.get("am_depth") and 0.70 < env["am_depth"] < 0.82,
                      {k: env.get(k) for k in ("carrier_hz", "am_depth", "envelope_max_v", "envelope_min_v")})

                # 5. pass/fail mask
                await call("scpi", commands=["WGEN:MOD OFF", "WGEN:FREQ 2000", "WGEN:NOIS:REL 0", "TIM:SCAL 2e-4",
                                             "TRIG:A:MODE NORM", "TRIG:A:EDGE:FILT:HFR ON"])
                mk = await call("mask", action="create", source="CH1", x_margin_div=0.02, y_margin_div=0.15)
                check("5", "mask learned", mk.get("ok"), mk)
                t1 = await call("mask", action="test", acquisitions=300, timeout_s=60)
                check("5", "clean signal passes", t1.get("ok") and t1.get("tested", 0) >= 300 and t1.get("failed", 1) == 0,
                      {k: t1.get(k) for k in ("tested", "failed", "acquisitions_per_s")})
                await call("scpi", commands=["WGEN:NOIS:REL 9"])
                t2 = await call("mask", action="test", acquisitions=300, timeout_s=60)
                check("5", "noisy signal fails", t2.get("ok") and t2.get("failed", 0) > 0,
                      {k: t2.get(k) for k in ("tested", "failed", "failure_rate", "failed_in_history")})
                if t2.get("failed"):
                    h = await call("history", action="harvest", sources=["CH1"], max_segments=5)
                    check("5", "failures kept in history", h.get("ok") and h.get("harvested", 0) > 0,
                          f"{h.get('available')} stored, {h.get('harvested')} harvested")
                await call("mask", action="off")
                await call("scpi", commands=["WGEN:NOIS:REL 0", "TRIG:A:EDGE:FILT:HFR OFF", "WGEN:OUTP OFF"])

                # 6. demo: start, status, blocked tools, stop
                d0 = await call("demo", action="start", level="full", dwell_s=10)
                check("6", "demo start is guarded", d0.get("error") == "guarded", d0.get("message", "")[:80])
                d1 = await call("demo", action="start", level="full", dwell_s=10, loops=0, confirm=True)
                check("6", "demo started", d1.get("ok") and d1.get("running"), d1.get("scenes"))
                await asyncio.sleep(25)
                st = await call("demo", action="status")
                blk = await call("scpi", commands="CHAN1:SCAL?")
                check("6", "running, other tools wait", st.get("running") and blk.get("ok") is False,
                      f"current {st.get('current')}; scpi -> {blk.get('message', '')[:60]}")
                sp = await call("demo", action="stop")
                res = sp.get("result") or {}
                check("6", "demo stopped and restored", not sp.get("running") and res.get("restored"),
                      {k: res.get(k) for k in ("scenes_played", "seconds", "restored", "problems")})
            finally:
                # 7. restore
                rs = await call("setup", action="restore", capture_id=base_id, confirm=True)
                after = await call("scpi", commands=["WGEN:OUTP?", "PGEN:PATT:STAT?", "DEV:MODE?", "MASK:STAT?"])
                vals = {x["sent"]: x.get("response") for x in after.get("results", [])}
                check("7", "setup restored", rs.get("ok") and not rs.get("instrument_errors"), vals)
                shot = await call("screenshot", note="after acceptance test")
                check("7", "screenshot returns an image", shot.get("_images") == 1, shot.get("path"))
                await call("connection", action="disconnect")


asyncio.run(main())
passed = sum(r["pass"] for r in results)
print(f"\n{passed}/{len(results)} checks passed")
json.dump(results, open(sys.argv[1] if len(sys.argv) > 1 else "/dev/null", "w"), indent=1, default=str)
