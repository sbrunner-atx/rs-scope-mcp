#!/usr/bin/env python3
"""Generate the field-tested SCPI API reference (Markdown + PDF) from the registry.

    uv run python scripts/gen_api_docs.py            # docs/rs-scope-api.md + .pdf
    uv run python scripts/gen_api_docs.py --no-pdf

The reference is built from the same registry the MCP server validates against:
the manual's command chapter, the firmware's own command tree (SYSTem:TREE?) and the
bench-verified overrides. PDF rendering uses pandoc (Markdown -> HTML) and WeasyPrint.
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rs_scope_mcp.instrument import GUARDS
from rs_scope_mcp.registry import Registry

DOCS = ROOT / "docs"
TITLE = "Rohde & Schwarz RTB2 / RTB2000 SCPI API: A Field-Tested Reference"
CHAPTERS = OrderedDict([
    ("16.3", "Common commands"), ("16.4", "Waveform setup"), ("16.5", "Trigger"),
    ("16.6", "Waveform analysis"), ("16.7", "Measurements"), ("16.8", "Applications"),
    ("16.9", "Documenting results"), ("16.10", "General instrument setup"),
    ("16.11", "Serial bus analysis"), ("16.12", "Logic analyzer (MSO)"),
    ("16.13", "Signal generation (B6)"), ("16.14", "Status reporting"),
])
KIND = {"query": "Q", "set": "S", "set_query": "S/Q", "event": "E", "undocumented": "U"}
WESTLAKE = "https://westlakeprofessionals.com"
REPO = "https://github.com/sbrunner-atx/rs-scope-mcp"


def esc(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def first_sentence(s: str, limit: int = 170) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*\u25cf\s*", "; ", s)
    m = re.match(r"(.+?[.!?])(\s|$)", s)
    out = m.group(1) if m else s
    return out if len(out) <= limit else out[: limit - 1].rsplit(" ", 1)[0] + "…"


def param_text(p: dict) -> str:
    bits = []
    name = p.get("name") or ""
    if p.get("enum"):
        vals = p["enum"]
        shown = " · ".join(f"`{v}`" for v in vals[:14]) + (" · …" if len(vals) > 14 else "")
        bits.append(shown)
    if p.get("range"):
        bits.append(f"range {esc(p['range'])}")
    if p.get("unit"):
        bits.append(f"unit {esc(p['unit'])}")
    if p.get("rst"):
        bits.append(f"*RST **{esc(p['rst'])}**")
    if not bits and p.get("description"):
        bits.append(esc(first_sentence(p["description"], 90)))
    label = f"`{name}` " if name else ""
    return label + "; ".join(bits)


def command_row(c) -> str:
    hdrs = "<br>".join(f"`{h}`" for h in c.all_headers)
    params = [param_text(p) for p in c.parameters]
    if c.query_parameters:
        params += ["query: " + param_text(p) for p in c.query_parameters]
    if c.returns and c.kind == "query":
        params += ["returns " + param_text(p) for p in c.returns]
    sfx = [f"`{s['name']}` {s.get('values') or ''}".strip() for s in c.suffixes
           if s.get("name") and not s.get("irrelevant")]
    if sfx:
        params.insert(0, "suffix " + ", ".join(sfx))
    desc = esc(first_sentence(c.summary or ""))
    flags = []
    if c.absent_in:
        flags.append(f"**Not implemented in FW {', '.join(sorted(c.absent_in))}.**")
    for n in c.notes:
        flags.append(f"Bench: {esc(n)}")
    if flags:
        desc += "<br>" + "<br>".join(flags)
    return f"| {hdrs} | {KIND.get(c.kind, c.kind)} | {'<br>'.join(params) or ''} | {desc} | {c.page or ''} |"


def build_markdown(reg: Registry) -> str:
    fw = reg.meta.get("firmware_trees", {})
    fw_line = ", ".join(f"{v}: {d['headers']:,} headers" for v, d in fw.items())
    manual = [c for c in reg.commands if c.source in ("manual", "bench")]
    undoc = [c for c in reg.commands if c.source == "firmware"]
    n_hdr = sum(len(c.all_headers) for c in manual)
    absent_tree = [c for c in manual if not c.firmware]
    L: list[str] = []
    a = L.append
    a(f"# {TITLE}\n")
    a("A complete, **bench-verified** reference to the SCPI remote-control interface of the Rohde & Schwarz "
      "RTB2 and RTB2000 oscilloscopes. It merges three sources: the command chapter of the user manual, the "
      "command list the firmware itself reports (`SYSTem:TREE?`), and behavior measured on a real instrument. "
      "Where the three disagree, this reference says so.\n")
    a(f"> **Tested against:** R&S RTB2004, firmware **03.000** (2024-10-15), all options installed "
      f"(K1, K2, K3, K15, B1, B6, B243, K36), on 2026-11-23. Manual: user manual 1333.1611.02-13 (FW 3.xx). "
      f"**{len(manual)} documented command blocks ({n_hdr} headers incl. aliases)**; firmware tree {fw_line}, "
      f"of which **{len(undoc)} undocumented command families** are listed in Appendix A.\n")
    a("> **RTB2 and RTB2000 are the same instrument.** The RTB2 is the rebranded RTB2000: identical hardware, a "
      "different enclosure color scheme, and firmware that runs on either. Everything here applies to the "
      "RTB2002 and RTB2004 under both names. The firmware has moved ahead of the manual several times; when in "
      "doubt, ask the instrument (`SYSTem:TREE?`, or `rs-scope tree-diff`).\n")
    a(f"> **Independent project, not affiliated with or endorsed by Rohde & Schwarz.** R&S and RTB2000 are "
      f"trademarks of Rohde & Schwarz GmbH & Co. KG. This reference is maintained alongside "
      f"[`rs-scope-mcp`]({REPO}). Corrections welcome.\n")

    a("## 1. Transport and connection\n")
    a("- **Raw SCPI socket:** TCP port **5025** (the instrument's *IP Port*). No VISA library is needed. "
      "VXI-11 is also offered on port 1024.")
    a("- **Discovery:** the instrument announces itself over mDNS as `_scpi-raw._tcp` "
      "(`Rohde&Schwarz RTB2004 - <serial>`). Setup: *Menu, Setup, Interface, Ethernet, Parameter*.")
    a("- **Framing:** commands and ASCII responses end with a newline. Binary data (waveforms, screenshots, "
      "files, settings) uses IEEE 488.2 definite-length blocks: `#<n><length><bytes>` then a newline.")
    a("- **Latency:** about 0.5 to 2 ms per simple query on a LAN. Many queries can share one line.\n")

    a("## 2. Command syntax\n")
    a("- Each node has a **long form** and a **short form** (the uppercase part): `CHANnel1:SCALe` = `CHAN1:SCAL`. "
      "Case does not matter.")
    a("- **Numeric suffixes** select instances: `CHANnel<m>` (1..4), `BUS<b>` (1..2), `MEASurement<m>` (1..8), "
      "`DIGital<m>` (0..15), frame and word indexes `<n>`, `<o>` (1..count). An omitted suffix means 1.")
    a("- **Optional nodes** are in brackets: `FORMat[:DATA]` accepts `FORM` and `FORM:DATA`.")
    a("- **Several commands per line:** separate with `;:`, for example `CHAN1:SCAL?;:TIM:SCAL?;*OPC?`. "
      "Responses come back `;`-separated in the same order.")
    a("- Type column below: **Q** query only, **S** set only, **S/Q** set and query, **E** event (no value), "
      "**U** undocumented (from the firmware tree).\n")

    a("## 3. Field-tested behavior (read this first)\n")
    a("These rules come from the bench, not the manual. They are built into `rs-scope-mcp`.\n")
    groups = [
        ("3.1 Talking to the instrument", [
            ("One client at a time.",
             ("The RTB2 serves a single LAN client on port 5025. A second connection (the instrument's web page in a "
             "browser, another script) is accepted but gets no answers until the first one closes. Share one "
             "connection inside a program.")),
            ("A rejected query sends no response at all.",
             ("An undefined header, a decode query on a bus of another type, or a value out of range simply "
             "produces silence until the client times out; the reason is in the error queue. Append `*OPC?` to every "
             "query (`X?;*OPC?`): the instrument still answers `1`, so a lone `1` means `X?` was rejected and "
             "`SYSTem:ERRor?` tells you why, within about 2 ms.")),
            ("Read the error queue after writes.",
             ("Settings are rejected silently too (`-221 Settings conflict`, `-222 Data out of range`). "
             "Drain `SYSTem:ERRor?` until `0,\"No error\"`, or use `SYSTem:ERRor:ALL?`.")),
            ("No value is 9.91E+37.",
             "Measurements and statistics report `9.91E+37` when there is nothing to measure. Treat it as NaN."),
        ]),
        ("3.2 Application modes", [
            ("Applications are exclusive modes, and switching takes time.",
             ("`DEVice:MODE` switches between YT, XY, FFT, mask test and others. The Bode plot reads back `BPL`, which "
             "the manual does not list. A mode change (`DEVice:MODE`, `SPECtrum:STATe`, `BPLot:ENABle`, "
             "`MASK:STATe`, `DISPlay:MODE`) takes about one second: send `*OPC?` after it before the next command. "
             "In BPL mode, bus decode and many settings fail with `-221`.")),
            ("The Bode application leaves changes behind.",
             ("Enabling it sets the memory to `ACQuire:MEMory LIMited` (75 kSa), changes channel scale and coupling, "
             "and sets the generator load to 50 ohm. Afterwards `ACQuire:MEMory AUTomatic` is refused with `-221`; "
             "only restoring a setup saved before the sweep (`SYSTem:SET`) brings full memory back. Leaving BPL "
             "switches the generator off.")),
            ("FFT mode owns the timebase.",
             ("Enable `SPECtrum` first, then set start and stop. Span, resolution bandwidth, window length and sample "
             "rate are coupled, so the instrument adjusts them in steps; apply start/stop again until they settle "
             "(two or three rounds on FW 03.000).")),
        ]),
        ("3.3 Acquisition, measurements and history", [
            ("Full memory needs a stopped acquisition.",
             "`...:DATA:POINts MAX` and `DMAX` only take effect when stopped. Use `SINGle;*OPC?` first."),
            ("Measure on a fresh acquisition.",
             ("Automatic measurements on a stopped record can return the values of an older screen. Run `SINGle;*OPC?` "
             "or keep the scope running before reading `MEASurement<m>:RESult?`.")),
            ("History queries need a stopped scope.",
             ("While acquiring, `HISTory` and `ACQuire:AVAilable?` queries are rejected (`-200`); send `STOP` first. "
             "The number of segments `ACQuire:NSINgle:COUNt` accepts depends on the record length (100 and 40 "
             "were refused with `-222` at full memory, 16 worked).")),
            ("History holds what it holds.",
             ("Fifty single acquisitions at 10 kSa left 40 segments; read `ACQuire:AVAilable?`. Out-of-range "
             "`HISTory:CURRent` indexes are clamped without an error; the valid range is `HISTory:STARt?` to "
             "`HISTory:STOP?` (0 is the newest).")),
        ]),
        ("3.4 Decoding and logic", [
            ("Decode queries need the matching bus type.",
             "`BUS<b>:I2C:...`, `BUS<b>:UART:...` and the rest only answer when `BUS<b>:TYPE` is that protocol."),
            ("Enable the decoder before the acquisition.",
             ("`BUS<b>:STATe ON` must be set before `SINGle`; frames of an acquisition taken earlier are not decoded. "
             "Keep the sample rate at least ten times the bit rate.")),
            ("Logic thresholds come in two parts.",
             ("`LOGic<p>:THReshold` takes the mode (`TTL`, `CMOS`, `ECL`, `USER`...), `LOGic<p>:THReshold:UDLevel<n>` "
             "the user level in volts. The manual merges them into one command.")),
            ("The pattern generator's test buses.",
             ("`PGENerator:FUNCtion` I2C, SPI and UART patterns on P0..P3 are useful references. The UART pattern runs "
             "at 115200 Bd and decodes cleanly as 9 data bits, no parity. The SPI pattern's chip select is "
             "pseudo-random, so frames can start or end incomplete. An unclocked parallel decode shows one-sample "
             "values while lines change; use the clocked parallel bus when a clock exists.")),
        ]),
        ("3.5 Generator", [
            ("The inputs are 1 Mohm only.",
             ("For 50 ohm signals use a feed-through terminator. `WGENerator:OUTPut:LOAD R50|HIGHz` does not change "
             "the 50 ohm source: it tells the generator what load to expect so the set amplitude appears there. "
             "Measured: 1 Vpp set with `R50` gives 1.030 Vpp at the terminator. By the same source impedance, `HIGHz` into "
             "a terminator reads half the set amplitude, and `R50` into an open 1 Mohm input reads double.")),
            ("Arbitrary waveforms: function first, then the file.",
             ("Send `WGENerator:FUNCtion ARBitrary` before `WGENerator:ARBitrary:FILE:NAME` and `...:FILE:OPEN`; "
             "otherwise opening fails with `-200`. A CSV file has the header `in s,C1 in V`, then one "
             "`time,value` row per point.")),
        ]),
        ("3.6 Mask test", [
            ("Learning a mask changes the acquisition.",
             ("`MASK:CHCopy` copies the channel into the mask and switches `ACQuire:TYPE` to `ENVelope`. Learn from an "
             "envelope of a few hundred milliseconds of the known-good signal, then check the new mask against that "
             "signal before testing anything else.")),
            ("Noisy signals need a stable trigger.",
             ("With noise on the signal, an edge trigger can fire on the wrong slope and the mask appears shifted by "
             "half a period. `TRIGger:A:EDGE:FILTer:HFReject ON` fixes it.")),
            ("`MASK:TEST RUN` sometimes answers `-221`.",
             ("Intermittently, after a mode change. `STOP`, `SINGle`, `RUN` and send it again; do not learn the mask "
             "again, because the signal on screen may be the one under test.")),
        ]),
        ("3.7 Setup and command tree", [
            ("Snapshot and restore the whole setup.",
             ("`SYSTem:SET?` returns the complete setup as a block of about 5 kB; sending it back with "
             "`SYSTem:SET <block>` restores it in about one second. `FORMat` is not part of it.")),
            ("The firmware command tree is useful but incomplete.",
             (f"`SYSTem:TREE?` (about 60 kB, 16 s) lists {fw_line.split(': ')[-1]} but omits working commands such "
             f"as `HCOPy:DATA?` and the bus `FRAMe<n>` queries. {len(absent_tree)} documented commands are not in the "
             "tree; most of them still work.")),
        ]),
    ]
    for heading, rules in groups:
        a(f"### {heading}\n")
        for title, body in rules:
            a(f"- **{title}** {body}")
        a("")
    a("All of these are checked by `scripts/acceptance_test.py`, a 40-check suite that drives the installed "
      "connector on a real instrument. On FW 03.000 all 40 pass.\n")

    a("## 4. Data transfer\n")
    a("`FORMat[:DATA]` selects `ASCii`, `REAL,32` or `UINTeger,8|16|32`; `FORMat:BORDer` the byte order "
      "(default MSB first). For UINT data: **volts = YORigin + YINCrement × code** "
      "(`...:DATA:YORigin?`, `...:DATA:YINCrement?`), verified on FW 03.000. The ADC has 10 bits "
      "(`YRESolution? = 10`), so `UINT,16` is lossless at half the size of `REAL,32`.\n")
    a("| Transfer (CH1, LAN) | Samples | Bytes | Time |")
    a("|---|---:|---:|---:|")
    for row in [("ASCii, DEFault", "130,432", "1.63 MB", "0.36 s"), ("REAL,32, DEFault", "130,432", "0.52 MB", "0.05 s"),
                ("UINT,8, MAXimum", "6,000,070", "6.0 MB", "0.55 s"), ("UINT,16, DMAXimum", "5,999,872", "12.0 MB", "0.65 s"),
                ("REAL,32, MAXimum", "6,000,070", "24.0 MB", "1.35 s"), ("Screenshot PNG 1280 × 824", "", "44 kB", "0.73 s"),
                ("UART frames, 3 fields each (batched)", "13 frames", "", "0.06 s (224 frames/s)"),
                ("Bode sweep 100 Hz to 1 MHz, 10 pts/decade", "41 points", "", "6.2 s")]:
        a(f"| {' | '.join(row)} |")
    a("")

    a("## 5. Commands that act outside the screen\n")
    a("`rs-scope-mcp` requires an explicit confirmation before sending these, because they drive signals into a "
      "circuit, discard settings, delete files, or can drop the network connection:\n")
    for g in GUARDS:
        pat = g.pattern.pattern.replace("^", "").replace("$", "").replace("\\[", "[").replace("\\]", "]") \
            .replace("\\*", "*").replace("\\?", "?")
        when = f" (when set to `{g.only_when.pattern.strip('^$()')}`)" if g.only_when else ""
        a(f"- `{pat}`{when}: {g.reason}.")
    a("")

    a("## 6. Command reference\n")
    a("Grouped by the manual's chapters. The last column is the manual page. "
      "\"Bench\" lines are verified behavior on FW 03.000.\n")
    by_chapter: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    titles: dict[str, str] = {}
    for c in manual:
        chap = ".".join(c.section.split(".")[:2]) if c.section else "16.?"
        sub = ".".join(c.section.split(".")[:3])
        by_chapter[chap][sub].append(c)
        titles.setdefault(sub, c.section_title if c.section.count(".") <= 2 else "")
        if c.section.count(".") == 2:
            titles[sub] = c.section_title
    num = 0
    for chap, chap_title in CHAPTERS.items():
        if chap not in by_chapter:
            continue
        num += 1
        a(f"### 6.{num} {chap_title}\n")
        for sub in sorted(by_chapter[chap], key=lambda s: [int(x) for x in s.split(".")]):
            cmds = by_chapter[chap][sub]
            t = titles.get(sub) or cmds[0].section_title
            if sub != chap:
                a(f"#### {t} ({sub})\n")
            a("| Command | Type | Parameters, values, *RST | Description | p. |")
            a("|---|---|---|---|---:|")
            for c in sorted(cmds, key=lambda c: c.page):
                a(command_row(c))
            a("")

    a("## Appendix A. Undocumented firmware commands\n")
    a(f"These {len(undoc)} command families are reported by `SYSTem:TREE?` on FW 03.000 but are not in the "
      "manual. Numeric suffixes are generalized (`CHAN<a>` stands for CHAN, CHAN2, CHAN3, CHAN4) with the range "
      "the firmware listed. Only the short form is known. Parameters are unknown and not validated; query "
      "first, and test on a scope you can reset.\n")
    groups: dict[str, list] = defaultdict(list)
    for c in undoc:
        root = re.split(r"[:<]", c.header.lstrip("*"), maxsplit=1)[0]
        groups[root].append(c)
    a("| Root | Commands |")
    a("|---|---|")
    for root in sorted(groups):
        items = []
        for c in sorted(groups[root], key=lambda c: c.header):
            sfx = ", ".join(f"{s['name']} {s['values']}" for s in c.suffixes if s.get("values"))
            items.append(f"`{c.header}`" + (f" ({sfx})" if sfx else ""))
        a(f"| `{root}` | {' · '.join(items)} |")
    a("")

    a("## Credits, license and commercial support\n")
    a(f"Generated from the [`rs-scope-mcp`]({REPO}) command registry by `scripts/gen_api_docs.py`. "
      "Command names, parameters and defaults are from the R&S RTB2 user manual (chapter 16) and from the "
      "instrument's firmware; behavior notes are from bench tests. Rohde & Schwarz is not responsible for this "
      "document.\n")
    a("**Free for hobbyists.** Personal use, amateur radio, education and nonprofit use of `rs-scope-mcp` and "
      "this reference are free (PolyForm Noncommercial License 1.0.0).\n")
    a(f"**Commercial use needs a license.** Any use by or for a business, including internal use on a company "
      f"test bench, needs a commercial license. Support for more instruments, custom skills and integrations, "
      f"and support plans are available too. Buy through **Westlake Professionals**: "
      f"[{WESTLAKE.replace('https://', '')}]({WESTLAKE}).\n")
    return "\n".join(L)


CSS = """
@page { size: Letter; margin: 18mm 16mm 18mm 16mm;
  @bottom-left { content: "RTB2 / RTB2000 SCPI API reference (rs-scope-mcp)"; font: 8pt -apple-system, Helvetica, sans-serif; color: #666; }
  @bottom-right { content: counter(page); font: 8pt -apple-system, Helvetica, sans-serif; color: #666; } }
@page :first { @bottom-left { content: none; } }
body { font: 9.5pt/1.45 -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif; color: #1f2328; }
h1 { font-size: 22pt; line-height: 1.15; border-bottom: 2px solid #0969da; padding-bottom: 6pt; margin-top: 0; }
h2 { font-size: 15pt; border-bottom: 1px solid #d0d7de; padding-bottom: 3pt; margin-top: 18pt; page-break-after: avoid; }
h3 { font-size: 12.5pt; margin-top: 14pt; page-break-after: avoid; color: #0b3d91; }
h2#command-reference ~ h3 { page-break-before: auto; }
h4 { font-size: 10.5pt; margin: 12pt 0 4pt; page-break-after: avoid; }
code { font: 8.3pt "SF Mono", Menlo, Consolas, monospace; background: #eff2f6; color: #0b3d91; padding: 0 2pt; border-radius: 3pt; }
blockquote { margin: 8pt 0; padding: 6pt 10pt; background: #ddf4ff; border-left: 4px solid #0969da; color: #1f2328; }
blockquote p { margin: 0; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0 8pt; font-size: 8pt; }
th { background: #f6f8fa; text-align: left; font-weight: 600; }
th, td { border: 1px solid #d0d7de; padding: 3pt 5pt; vertical-align: top; }
tr { page-break-inside: avoid; }
td:first-child { width: 30%; } td:nth-child(2) { width: 5%; white-space: nowrap; } td:nth-child(5) { width: 4%; text-align: right; }
td code { font-size: 7.6pt; }
a { color: #0969da; text-decoration: none; }
nav.toc { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6pt; padding: 8pt 14pt; margin: 10pt 0 14pt; page-break-after: always; }
nav.toc h2 { border: 0; margin: 0 0 6pt; font-size: 12pt; }
nav.toc ol { list-style: none; padding: 0; margin: 0; }
nav.toc li { margin: 2pt 0; }
nav.toc li.l3 { padding-left: 14pt; font-size: 9pt; }
nav.toc a { color: #1f2328; }
nav.toc a::after { content: leader('.') target-counter(attr(href), page); color: #666; }
"""


def build_pdf(md_path: Path, pdf_path: Path) -> None:
    if not shutil.which("pandoc") or not shutil.which("weasyprint"):
        raise SystemExit("pandoc and weasyprint are needed for the PDF (brew install pandoc weasyprint)")
    body = subprocess.run(["pandoc", "-f", "gfm", "-t", "html5", str(md_path)], check=True,
                          capture_output=True, text=True).stdout
    # contents with page numbers: h2 and h3 headings
    heads = re.findall(r'<h([23]) id="([^"]+)">(.*?)</h\1>', body, re.DOTALL)
    items = "".join(f'<li class="l{lvl}"><a href="#{hid}">{re.sub("<[^>]+>", "", txt)}</a></li>'
                    for lvl, hid, txt in heads if lvl == "2" or "command-reference" not in hid)
    toc = f'<nav class="toc"><h2>Contents</h2><ol>{items}</ol></nav>'
    first_h2 = body.find("<h2")
    body = body[:first_h2] + toc + body[first_h2:]
    doc = (f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(TITLE)}</title>'
           f"<style>{CSS}</style></head><body>{body}</body></html>")
    html_path = pdf_path.with_suffix(".html")
    html_path.write_text(doc, encoding="utf-8")
    subprocess.run(["weasyprint", str(html_path), str(pdf_path)], check=True, capture_output=True)
    html_path.unlink()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-pdf", action="store_true")
    a = ap.parse_args(argv)
    reg = Registry.default()
    md = build_markdown(reg)
    md_path = DOCS / "rs-scope-api.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"wrote {md_path} ({len(md) // 1024} kB)")
    if not a.no_pdf:
        pdf_path = DOCS / "rs-scope-api.pdf"
        build_pdf(md_path, pdf_path)
        print(f"wrote {pdf_path} ({pdf_path.stat().st_size // 1024} kB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
