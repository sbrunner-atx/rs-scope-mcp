"""Acceptance test on real hardware: drive the MCP server over stdio, as Claude Desktop does.

Wiring: Aux Out -> 50 ohm feed-through terminator -> CH1; pattern generator P0..P3 -> logic
probe D0..D3. Needs options B1, B6, K1, K2, K15, K36.

    RS_SCOPE_HOST=<ip> uv run python scripts/acceptance_test.py [results.json]
    RS_SCOPE_EXT_DIR=<installed extension folder> ...     test an installed .mcpb instead of this repo
    ACCEPTANCE_ONLY=A,C ...                                run only some groups

The setup is saved first and restored at the end; every test runs on its own, so one failure
does not stop the rest.
"""
# ruff: noqa: SIM117
import asyncio
import json
import os
import sys
import time
import traceback
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXT = os.environ.get("RS_SCOPE_EXT_DIR", str(Path(__file__).resolve().parents[1]))
ENV = dict(os.environ, RS_SCOPE_HOST=os.environ.get("RS_SCOPE_HOST", ""),
           RS_SCOPE_PORT=os.environ.get("RS_SCOPE_PORT", "5025"),
           RS_SCOPE_DATA_DIR=os.environ.get("RS_SCOPE_DATA_DIR", ""))
ONLY = set(filter(None, os.environ.get("ACCEPTANCE_ONLY", "").split(",")))
results: list[dict] = []

GEN = ["DEV:MODE YT", "PGEN:PATT:STAT OFF", "LOG1:STAT OFF", "BUS1:STAT OFF", "CHAN1:STAT ON", "CHAN1:COUP DCL",
       "CHAN1:SCAL 0.2", "CHAN1:POS 0", "CHAN1:OFFS 0", "WGEN:OUTP:LOAD R50", "WGEN:MOD OFF", "WGEN:NOIS:REL 0",
       "WGEN:BURS OFF", "WGEN:SWE OFF", "WGEN:VOLT 1", "WGEN:VOLT:OFFS 0", "TIM:REF 50", "TIM:POS 0",
       "TRIG:A:SOUR CH1", "TRIG:A:TYPE EDGE", "TRIG:A:EDGE:SLOP POS", "TRIG:A:LEV1 0", "TRIG:A:MODE AUTO",
       "TRIG:A:EDGE:FILT:HFR OFF"]
LOGIC = ["DEV:MODE YT", "WGEN:OUTP OFF", "CHAN1:STAT OFF", "LOG1:STAT ON", "LOG1:THR USER", "LOG1:THR:UDL 0.5",
         "TIM:REF 8.33", "TRIG:A:TYPE EDGE", "TRIG:A:MODE NORM", "BUS1:STAT ON", "BUS1:FORM ASC"]


def check(group, name, ok, detail):
    results.append({"group": group, "check": name, "pass": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {group} {name}: {str(detail)[:230]}", flush=True)


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

            async def scpi(cmds, confirm=False):
                return await call("scpi", commands=cmds, confirm=confirm)

            async def q(cmd):
                r = await scpi(cmd)
                return (r.get("results") or [{}])[0].get("response")

            # ------------------------------------------------------------------ A foundation
            async def group_a():
                tools = (await s.list_tools()).tools
                check("A", "16 tools listed", len(tools) == 16, [t.name for t in tools])
                hits = await call("scpi_search", query="i2c frame data", limit=5)
                check("A", "scpi_search", any(h["header"] == "BUS<b>:I2C:FRAMe<n>:DATA?" for h in hits["commands"]),
                      [h["header"] for h in hits["commands"]])
                d = await call("scpi_describe", header="DEV:MODE")
                check("A", "scpi_describe with bench notes", "BPL" in d["parameters"][0]["enum"] and d.get("notes"),
                      d["parameters"][0]["enum"])
                bad = await scpi("FOO:BAR 1")
                check("A", "unknown header refused", bad.get("error") == "validation", bad.get("message"))
                bad = await scpi("CHAN5:SCAL 1")
                check("A", "bad suffix refused", bad.get("error") == "validation", bad.get("message"))
                bad = await scpi("TRIG:A:HYST?")
                check("A", "firmware-absent command refused", bad.get("error") == "validation"
                      and "not implemented" in bad.get("message", ""), bad.get("message"))
                und = await scpi("MEAS1:RES:BUFF:AVG?")
                check("A", "undocumented command answers", und.get("ok") and und["results"][0].get("response"),
                      und.get("results"))
                await scpi("BUS1:TYPE SPI")
                t0 = time.monotonic()
                rej = await scpi("BUS1:I2C:FCO?")
                dt = time.monotonic() - t0
                errs = (rej.get("results") or [{}])[0].get("instrument_errors")
                check("A", "wrong-bus query fails fast with reason", not rej.get("ok") and errs and dt < 1.0,
                      f"{errs} in {dt * 1000:.0f} ms")
                before = await q("CHAN1:SCAL?")
                g = await scpi(["CHAN1:SCAL 0.5", "*RST"])
                after = await q("CHAN1:SCAL?")
                check("A", "guarded batch sends nothing", g.get("error") == "guarded" and before == after,
                      f"{before} -> {after}")
                st = await call("setup", action="show")
                check("A", "setup show", st.get("ok") and len(st.get("settings", {})) >= 20, len(st.get("settings", {})))
                sq = await call("data", action="sql", sql="delete from frames")
                check("A", "SQL is read-only", not sq.get("ok"), sq.get("message"))

            # ------------------------------------------------------------------ B analog
            async def group_b():
                await scpi(GEN + ["WGEN:FUNC SIN", "WGEN:FREQ 1000", "TIM:SCAL 5e-4", "WGEN:OUTP ON"], confirm=True)
                await asyncio.sleep(1)
                m = await call("measure", source="CH1", types=["FREQ", "PEAK", "RMS"])
                v = m.get("values", {})
                check("B", "sine 1 kHz / 1 Vpp / 0.354 Vrms",
                      v.get("FREQ") and abs(v["FREQ"] - 1000) < 5 and abs((v.get("PEAK") or 0) - 1) < 0.06
                      and abs((v.get("RMS") or 0) - 0.3536) < 0.02, v)
                cap = await call("capture", sources=["CH1"], note="sine")
                s1 = cap.get("sources", {}).get("CH1", {})
                check("B", "capture summary", abs((s1.get("frequency_hz") or 0) - 1000) < 5, s1)
                ser = await call("data", action="series", capture_id=cap["capture"], source="CH1", max_points=10)
                check("B", "series drill-down", ser.get("ok") and 0 < len(ser.get("rows", [])) <= 10,
                      f"{len(ser.get('rows', []))} rows of {ser.get('samples')} samples")
                await scpi(["WGEN:FUNC SQU", "WGEN:FREQ 100000", "TIM:SCAL 5e-8", "TRIG:A:MODE NORM"])
                m = await call("measure", source="CH1", types=["RTIM", "POV", "AMPL"])
                v = m.get("values", {})
                check("B", "edge rise time and overshoot", v.get("RTIM") and v["RTIM"] < 2e-8, v)
                await scpi(["WGEN:FREQ 10000", "TRIG:A:MODE AUTO"])
                sp = await call("spectrum", source="CH1", start_hz=0, stop_hz=2e5)
                f = [p["freq_hz"] for p in sp.get("peaks", [])]
                check("B", "square-wave odd harmonics",
                      all(any(abs(x - h) < 600 for x in f) for h in (1e4, 3e4, 5e4)), f)
                await scpi(["SPEC OFF", "WGEN:FUNC SIN", "WGEN:FREQ 100000", "WGEN:MOD:TYPE AM", "WGEN:MOD:FUNC SIN",
                            "WGEN:MOD:AM:FREQ 1000", "WGEN:MOD:AM:DEPT 80", "WGEN:MOD ON", "TIM:SCAL 1e-3"])
                cap = await call("capture", sources=["CH1"], note="AM 80 %")
                env = await call("data", action="envelope", capture_id=cap["capture"], source="CH1")
                check("B", "AM depth ~75 % (set 80 %)", env.get("am_depth") and 0.70 < env["am_depth"] < 0.82, env.get("am_depth"))
                await scpi(["WGEN:MOD:FUNC SQU", "WGEN:MOD:AM:FREQ 200", "WGEN:MOD:AM:DEPT 100", "TIM:SCAL 2e-3"])
                cap = await call("capture", sources=["CH1"], note="keyed carrier")
                env = await call("data", action="envelope", capture_id=cap["capture"], source="CH1")
                check("B", "hard keying detected", env.get("rise_10_90_s") is not None and env["rise_10_90_s"] < 1e-4,
                      {k: env.get(k) for k in ("rise_10_90_s", "fall_90_10_s", "am_depth")})
                await scpi(["WGEN:MOD OFF", "WGEN:OUTP OFF"])
                b = await call("bode", start_hz=100, stop_hz=1e6, points_per_decade=10, input_channel="CH1",
                               output_channel="CH1", confirm=True)
                check("B", "Bode loopback flat", b.get("ok") and b.get("shape") == "flat within 6 dB"
                      and abs(b.get("peak_gain_db") or 0) < 0.5, {k: b.get(k) for k in ("points", "shape", "seconds")})

            # ------------------------------------------------------------------ C logic
            async def group_c():
                async def survey(func, trig, tb="1e-4", slope="NEG"):
                    await scpi(LOGIC + [f"PGEN:FUNC {func}", f"TIM:SCAL {tb}", f"TRIG:A:SOUR {trig}",
                                        f"TRIG:A:EDGE:SLOP {slope}", "PGEN:PATT:STAT ON"], confirm=True)
                    await asyncio.sleep(1.0)  # the pattern generator needs a moment to switch patterns
                    cap = await call("capture", sources=["POD1"], note=f"unknown ({func})")
                    return await call("data", action="identify", capture_id=cap["capture"], source="D0-D7")

                # I2C (also proves decode works after the Bode application in group B)
                ident = await survey("I2C", "D0")
                check("C", "I2C identified", ident.get("guess") == "I2C", ident.get("decoder_setup"))
                await scpi(["BUS1:TYPE I2C", "BUS1:I2C:CLOC:SOUR D0", "BUS1:I2C:DATA:SOUR D1"])
                dec = (await call("capture", sources=["BUS1"]))["sources"]["BUS1"]
                okf = (dec.get("status") or {}).get("OK", 0)
                check("C", "I2C decodes OK", okf >= 1 and "idn?" in dec.get("ascii", "") and "Schwarz" in dec.get("ascii", ""),
                      f"{dec.get('status')} {dec.get('ascii')!r}")
                # SPI
                ident = await survey("SPI", "D3")
                setup = ident.get("decoder_setup") or {}
                check("C", "SPI identified with chip select", ident.get("guess") == "SPI" and setup.get("CS:SOURce") == "D3", setup)
                mosi, miso = (setup.get("MOSI/MISO:SOURce") or ["D1", "D2"])[:2]
                await scpi(["BUS1:TYPE SPI", f"BUS1:SPI:CLOC:SOUR {setup.get('CLOCk:SOURce', 'D0')}",
                            f"BUS1:SPI:CS:SOUR {setup.get('CS:SOURce', 'D3')}",
                            f"BUS1:SPI:CS:POL {setup.get('CS:POLarity', 'NEG')}", f"BUS1:SPI:MOSI:SOUR {mosi}",
                            f"BUS1:SPI:MISO:SOUR {miso}", "BUS1:SPI:SSIZ 8", "TIM:SCAL 1e-3"])
                dec = (await call("capture", sources=["BUS1"]))["sources"]["BUS1"]
                # the pattern generator toggles chip select pseudo-randomly, so frames end mid-text
                # (status INCL); a good decode has at least one complete frame
                okf = (dec.get("status") or {}).get("OK", 0)
                check("C", "SPI decodes complete frames", dec.get("frames", 0) >= 1 and okf >= 1,
                      f"{dec.get('status')} {dec.get('first', [{}])[0].get('mosi')}")
                # UART
                ident = await survey("UART", "D0")
                check("C", "UART identified at 115200 Bd", ident.get("guess") == "UART" and ident.get("baud_standard") == 115200,
                      {k: ident.get(k) for k in ("baud_measured", "polarity")})
                found = None
                for ss in ("8", "9"):
                    await scpi(["BUS1:TYPE UART", "BUS1:UART:DATA:SOUR D0", "BUS1:UART:BAUD 115200", "BUS1:UART:POL IDLH",
                                f"BUS1:UART:SSIZ {ss}", "BUS1:UART:PAR NONE", "BUS1:UART:SBIT B1", "TIM:SCAL 2e-4",
                                "TIM:REF 8.33"])
                    dec = (await call("capture", sources=["BUS1"]))["sources"]["BUS1"]
                    if "idn" in dec.get("ascii", ""):
                        found = ss
                        break
                check("C", "UART decodes *idn?", found is not None, f"word size {found}: {dec.get('ascii')!r}")
                # servo
                await scpi(LOGIC + ["BUS1:STAT OFF", "PGEN:FUNC SQU", "PGEN:PATT:FREQ 50", "PGEN:PATT:SQU:DCYC 7.5",
                                    "PGEN:PATT:SQU:POL NORM", "TIM:SCAL 1e-2", "TIM:REF 50", "TRIG:A:SOUR D0",
                                    "TRIG:A:EDGE:SLOP POS", "TRIG:A:MODE AUTO", "PGEN:PATT:STAT ON"], confirm=True)
                await asyncio.sleep(1)
                cap = await call("capture", sources=["POD1"], note="servo")
                ident = await call("data", action="identify", capture_id=cap["capture"], source="D0-D7")
                m = await call("measure", source="D0", types=["FREQ", "PPW"])
                v = m.get("values", {})
                check("C", "servo PWM identified and measured", "servo" in ident.get("guess", "")
                      and abs((v.get("FREQ") or 0) - 50) < 1 and 0.0013 < (v.get("PPW") or 0) < 0.0018,
                      f"{ident.get('guess')}; {v}")
                # counter as a parallel bus
                await scpi(LOGIC + ["PGEN:FUNC COUN", "PGEN:PATT:COUN:FREQ 100000", "PGEN:PATT:COUN:DIR UPW",
                                    "TIM:SCAL 2e-5", "TIM:REF 50", "TRIG:A:SOUR D3", "TRIG:A:EDGE:SLOP POS", "BUS1:FORM HEX",
                                    "BUS1:TYPE PAR", "BUS1:PAR:WIDT 4", "BUS1:PAR:DATA0:SOUR D0", "BUS1:PAR:DATA1:SOUR D1",
                                    "BUS1:PAR:DATA2:SOUR D2", "BUS1:PAR:DATA3:SOUR D3", "PGEN:PATT:STAT ON"], confirm=True)
                cap = await call("capture", sources=["BUS1"], note="counter")
                fr = await call("data", action="frames", capture_id=cap["capture"], bus=1, limit=12)
                vals = [int(row[10], 16) for row in fr.get("rows", []) if row[10]]
                # an unclocked parallel decode also catches pins mid-transition (15 -> 8 -> 0): every
                # value must be the next count or a glitch on the way to it
                ok = len(vals) >= 8
                prev = vals[0] if vals else 0
                for v in vals[1:]:
                    if v == (prev + 1) % 16:
                        prev = v
                    elif (v & ~((prev + 1) % 16 ^ prev)) != (prev & ~((prev + 1) % 16 ^ prev)):
                        ok = False
                check("C", "counter counts up (allowing transition glitches)", ok, vals)
                # I2C across segmented memory
                await scpi(LOGIC + ["PGEN:FUNC I2C", "TIM:SCAL 2e-4", "TRIG:A:SOUR D0", "TRIG:A:EDGE:SLOP NEG",
                                    "BUS1:TYPE I2C", "BUS1:I2C:CLOC:SOUR D0", "BUS1:I2C:DATA:SOUR D1", "ACQ:NSIN:COUN 8",
                                    "PGEN:PATT:STAT ON"], confirm=True)
                await call("scpi", commands="RUNS", wait=True)
                h = await call("history", action="harvest", sources=["BUS1"], max_segments=8)
                rows = h.get("segments", [])
                ok_rows = [r for r in rows if isinstance(r.get("BUS1"), dict) and r["BUS1"].get("status") == {"OK": 2}]
                check("C", "I2C decoded in every segment", len(rows) >= 6 and len(ok_rows) == len(rows),
                      f"{len(ok_rows)}/{len(rows)} segments OK")

            # ------------------------------------------------------------------ D mask
            async def group_d():
                await scpi(GEN + ["WGEN:FUNC SIN", "WGEN:FREQ 2000", "TIM:SCAL 2e-4", "TRIG:A:MODE NORM",
                                  "TRIG:A:EDGE:FILT:HFR ON", "WGEN:OUTP ON"], confirm=True)
                mk = await call("mask", action="create", source="CH1", x_margin_div=0.02, y_margin_div=0.15)
                shot = await call("screenshot", note="mask learned")
                check("D", "mask learned and self-checked", mk.get("ok") and mk.get("self_check", {}).get("failed") == 0,
                      f"{mk} screen {shot.get('path')}")
                t1 = await call("mask", action="test", acquisitions=300, timeout_s=60)
                check("D", "clean signal passes", t1.get("tested", 0) >= 300 and t1.get("failed") == 0,
                      {k: t1.get(k) for k in ("tested", "failed")})
                await scpi(["WGEN:NOIS:REL 9"])
                t2 = await call("mask", action="test", acquisitions=300, timeout_s=60)
                check("D", "noisy signal fails", (t2.get("failed") or 0) > 0, {k: t2.get(k) for k in ("tested", "failed")})
                h = await call("history", action="harvest", sources=["CH1"], max_segments=5)
                check("D", "failures kept in history", h.get("ok") and h.get("harvested", 0) > 0,
                      f"{h.get('available')} stored, {h.get('harvested')} harvested")
                await call("mask", action="off")
                await scpi(["WGEN:NOIS:REL 0", "TRIG:A:EDGE:FILT:HFR OFF", "WGEN:OUTP OFF"])

            # ------------------------------------------------------------------ E demo
            async def group_e():
                d0 = await call("demo", action="start", level="analog")
                check("E", "demo start is guarded", d0.get("error") == "guarded", d0.get("message", "")[:60])
                d1 = await call("demo", action="start", level="analog", dwell_s=3, loops=1, confirm=True)
                check("E", "analog demo started", d1.get("ok"), d1.get("scenes"))
                await asyncio.sleep(3)
                blk = await scpi("CHAN1:SCAL?")
                check("E", "other tools wait", blk.get("ok") is False and "demo" in blk.get("message", ""), blk.get("message", "")[:60])
                for _ in range(120):
                    st = await call("demo", action="status")
                    if not st.get("running"):
                        break
                    await asyncio.sleep(2)
                res = st.get("result") or {}
                check("E", "full analog pass, restored, no problems",
                      res.get("scenes_played") == 8 and res.get("restored") and not res.get("problems"),
                      {k: res.get(k) for k in ("scenes_played", "seconds", "restored", "problems")})

            # ------------------------------------------------------------------ run
            c = await call("connection", action="connect")
            check("0", "connect", c.get("ok"), f"{c.get('idn', {}).get('model')} FW {c.get('idn', {}).get('firmware')} {c.get('options')}")
            ses = await call("session", action="start", goal="acceptance test")
            base = await call("setup", action="save", note="before acceptance test")
            try:
                for name, fn in (("A", group_a), ("B", group_b), ("C", group_c), ("D", group_d), ("E", group_e)):
                    if ONLY and name not in ONLY:
                        continue
                    t0 = time.monotonic()
                    try:
                        await fn()
                    except Exception as e:  # noqa: BLE001
                        check(name, "group crashed", False, f"{e}\n{traceback.format_exc()[-400:]}")
                    print(f"    group {name}: {time.monotonic() - t0:.0f} s", flush=True)
            finally:
                rs = await call("setup", action="restore", capture_id=base.get("capture"), confirm=True)
                after = await scpi(["WGEN:OUTP?", "DEV:MODE?", "MASK:STAT?"])
                check("Z", "setup restored", rs.get("ok"), {x["sent"]: x.get("response") for x in after.get("results", [])})
                caps = await call("data", action="captures", limit=200)
                n = len(caps.get("captures", []))
                one = await call("data", action="capture", capture_id=caps["captures"][-1]["id"]) if n else {}
                has_setup = (Path(one.get("files", "/nonexistent")) / "setup.set").exists() if one else False
                check("Z", "session captures stored with setup", n >= 10 and has_setup, f"{n} captures in {ses.get('session')}")
                shot = await call("screenshot", note="after acceptance test")
                check("Z", "screenshot", shot.get("_images") == 1, shot.get("path"))
                await call("connection", action="disconnect")


t_start = time.monotonic()
asyncio.run(main())
passed = sum(r["pass"] for r in results)
print(f"\n{passed}/{len(results)} checks passed in {time.monotonic() - t_start:.0f} s")
if len(sys.argv) > 1:
    Path(sys.argv[1]).write_text(json.dumps(results, indent=1, default=str))
sys.exit(0 if passed == len(results) else 1)
