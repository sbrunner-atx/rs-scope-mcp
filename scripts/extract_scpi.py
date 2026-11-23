#!/usr/bin/env python3
"""Build the RTB2 SCPI command registry from the R&S RTB2 User Manual PDF.

The registry (src/rs_scope_mcp/data/rtb2/commands.json) is the foundation of rs-scope-mcp:
every remote command the instrument documents, with its suffixes, parameters,
enum values, ranges, *RST defaults, units, usage (query/set/event) and the
manual page it came from.

It is generated from your own copy of the manual so the project never ships the
manual itself:

    uv run python scripts/extract_scpi.py "/path/to/R&S RTB2 User Manual v13 ....pdf"

The parser relies on the manual's typesetting, which is very regular:
  * command headings are Arial-Bold 10pt at the left text margin (x ~ 147 pt)
  * field labels ("Suffix:", "Parameters:", "Usage:" ...) are bold at that margin too
  * field values sit in a second column (x ~ 247 pt)
  * each subsection opens with an index of its commands (9pt, dot leaders),
    which we use as ground truth for a completeness check.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pymupdf  # PyMuPDF

FIRST_PAGE = 301  # chapter 16 "Remote control commands" (printed == PDF page)
LAST_PAGE = 567   # end of 16.14 status reporting
LEFT_X = (140, 160)
VALUE_X = 240
HEADER_Y = 75
FOOTER_Y = 770

LABELS = {
    "Suffix:", "Parameters:", "Return values:", "Setting parameters:",
    "Query parameters:", "Parameters for setting and query:", "Usage:",
    "Example:", "Manual operation:", "Firmware/Software:", "Options:",
    "Query parameter:", "Setting parameter:", "Return value:", "Examples:",
    "Note:", "Notes:",
}
LABEL_KEY = {
    "Suffix:": "suffix",
    "Parameters:": "parameters",
    "Setting parameters:": "parameters",
    "Setting parameter:": "parameters",
    "Parameters for setting and query:": "parameters",
    "Query parameters:": "query_parameters",
    "Query parameter:": "query_parameters",
    "Return values:": "returns",
    "Return value:": "returns",
    "Usage:": "usage",
    "Example:": "example",
    "Examples:": "example",
    "Manual operation:": "manual",
    "Firmware/Software:": "firmware",
    "Options:": "options",
    "Note:": "notes",
    "Notes:": "notes",
}
CMD_RE = re.compile(r"^(\*[A-Z]{2,4}\??|[A-Za-z\[][A-Za-z0-9<>:\[\]]*\??)$")
SECTION_RE = re.compile(r"^(16(?:\.\d+)+)\s*$")
PARAM_NAME_RE = re.compile(r"^<[^>]+>$")
INDEX_DOTS_RE = re.compile(r"\.{6,}")


# --------------------------------------------------------------------------- rows
def page_rows(page):
    """Group a page's spans into visual rows: [(y, [(x, text, font, size)...])]."""
    spans = []
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            for s in line["spans"]:
                t = s["text"]
                if not t.strip():
                    continue
                x0, y0 = s["bbox"][0], s["bbox"][1]
                if y0 < HEADER_Y or y0 > FOOTER_Y:
                    continue
                spans.append((round(y0), x0, t, s["font"], s["size"]))
    spans.sort()
    rows = []
    for y, x, t, font, size in spans:
        if rows and abs(rows[-1][0] - y) <= 2:
            rows[-1][1].append((x, t, font, size))
        else:
            rows.append([y, [(x, t, font, size)]])
    for r in rows:
        r[1].sort()
    return rows


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("‑", "-").replace("–", "-")).strip()


# --------------------------------------------------------------------------- parse
def parse(pdf_path: Path):
    doc = pymupdf.open(pdf_path)
    commands = []        # list of dict
    index_entries = []   # (command, page_ref, section)
    section = ""
    section_title = ""
    cur = None           # current command dict
    field = None         # current field key

    def close():
        nonlocal cur
        if cur:
            commands.append(cur)
        cur = None

    for pno in range(FIRST_PAGE - 1, LAST_PAGE):
        page = doc[pno]
        printed = pno + 1
        for y, spans in page_rows(page):
            x0, t0, f0, s0 = spans[0]
            text = norm("".join(t for _, t, _, _ in spans))
            left = norm("".join(t for x, t, _, _ in spans if x < VALUE_X))
            right = norm("".join(t for x, t, _, _ in spans if x >= VALUE_X))
            bold0 = "Bold" in f0

            # Section headings (16.x.y Title) -- larger font
            m = SECTION_RE.match(t0.strip())
            if m and (s0 >= 10.5 or bold0):
                close()
                section = m.group(1)
                section_title = norm("".join(t for _, t, _, _ in spans[1:]))
                field = None
                continue
            if s0 >= 11.5 and not bold0:
                continue

            # Command index lines (9pt with dot leaders)
            if s0 < 9.5 and LEFT_X[0] <= x0 <= LEFT_X[1] and INDEX_DOTS_RE.search(text):
                name = INDEX_DOTS_RE.split(text)[0].strip()
                pg = re.findall(r"(\d+)\s*$", text)
                index_entries.append({"command": name, "page": int(pg[0]) if pg else None,
                                      "section": section})
                continue
            if s0 < 9.5 and LEFT_X[0] <= x0 <= LEFT_X[1] and CMD_RE.match(t0.strip()):
                # index entry wrapped without dots on the same row
                continue

            at_margin = LEFT_X[0] <= x0 <= LEFT_X[1]

            # Field labels
            if at_margin and bold0 and t0.strip() in LABELS:
                if cur is None:
                    continue
                field = LABEL_KEY[t0.strip()]
                if right:
                    add_value(cur, field, None, right, spans)
                continue

            # Command heading(s)
            if at_margin and bold0 and s0 >= 9.5:
                head = t0.strip()
                rest = norm("".join(t for _, t, _, _ in spans[1:]))
                if CMD_RE.match(head) and head not in LABELS and not head.endswith(":"):
                    if cur is not None and field is None and not cur["summary_lines"]:
                        # stacked alias heading belonging to the same block
                        cur["headers"].append({"header": head, "signature": rest})
                        continue
                    close()
                    cur = {
                        "headers": [{"header": head, "signature": rest}],
                        "section": section,
                        "section_title": section_title,
                        "page": printed,
                        "summary_lines": [],
                        "suffix": [],
                        "parameters": [],
                        "query_parameters": [],
                        "returns": [],
                        "usage": [],
                        "example": [],
                        "manual": [],
                        "firmware": [],
                        "options": [],
                        "notes": [],
                    }
                    field = None
                    continue

            if cur is None:
                continue

            if field is None:
                # description text directly below the heading(s)
                if at_margin:
                    cur["summary_lines"].append(text)
                continue

            # inside a field: a left-column token starts a new item (e.g. "<m>", "<Scale>")
            if at_margin and left:
                add_value(cur, field, left, right, spans)
            elif x0 >= VALUE_X - 10:
                add_value(cur, field, None, right or text, spans)
            else:
                # left-margin prose after fields (notes) -> attach to summary tail
                cur["summary_lines"].append(text)
                field = None
    close()
    return commands, index_entries


def add_value(cur, field, name, value, spans):
    items = cur[field]
    mono = any("Courier" in f for _, _, f, _ in spans)
    boldval = any("Bold" in f for x, _, f, _ in spans if x >= VALUE_X)
    if field in ("usage", "manual", "firmware", "options", "notes"):
        if value:
            items.append(value)
        return
    if field == "example":
        items.append(value if not name else f"{name} {value}".strip())
        return
    if name and PARAM_NAME_RE.match(name.split()[0]):
        items.append({"name": name.split()[0], "lines": [value] if value else [],
                      "bold": [value] if boldval and value else []})
        return
    if name and not items:
        items.append({"name": name, "lines": [value] if value else [], "bold": []})
        return
    if not items:
        items.append({"name": None, "lines": [], "bold": []})
    if value:
        items[-1]["lines"].append(value)
        if boldval:
            items[-1]["bold"].append(value)
    _ = mono


# --------------------------------------------------------------------------- refine
ENUM_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*(\s*\|\s*[A-Z0-9][A-Za-z0-9_]*)+$")


_TOKEN_RE = re.compile(r'^"{0,2}[A-Za-z0-9_][A-Za-z0-9_.]*"{0,2}$')
_RANGE_TOKEN_RE = re.compile(r"^([A-Za-z]+)(\d+)\.\.([A-Za-z]*)(\d+)$")


def _expand(tok: str) -> list[str]:
    m = _RANGE_TOKEN_RE.match(tok)
    if not m:
        return [tok.strip('"')]
    pfx, lo, _, hi = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
    if hi < lo or hi - lo > 32:
        return [tok]
    return [f"{pfx}{n}" for n in range(lo, hi + 1)]


def parse_enum(line: str):
    """Parse 'A | B | C1..4 trailing prose' -> (['A','B','C1',...,'C4'], 'trailing prose')."""
    text = re.sub(r"\s*\|?\s*See also:.*$", "", line).strip()
    if "|" not in text:
        return None
    parts = [p.strip() for p in text.split("|")]
    if any(not p for p in parts):
        return None
    head = parts[:-1]
    last_words = parts[-1].split(None, 1)
    if not last_words:
        return None
    vals = head + [last_words[0]]
    rest = last_words[1] if len(last_words) > 1 else ""
    if not all(_TOKEN_RE.match(v) for v in vals):
        return None
    out: list[str] = []
    for v in vals:
        out.extend(_expand(v))
    return out, rest


def refine_item(item):
    """Turn raw lines of a parameter/suffix/return block into structured fields."""
    out = {"name": item["name"]}
    desc, enum, extra = [], None, {}
    value_docs = {}
    cur_val = None
    last_key = None
    bold = set(item.get("bold", []))
    # merge enum lists that wrap over several lines ("A | B |" + "C | D")
    lines = []
    for ln in item["lines"]:
        if lines and (lines[-1].rstrip().endswith("|") or ln.lstrip().startswith("|")) \
                and lines[-1] not in bold:
            lines[-1] = lines[-1].rstrip() + " " + ln.strip()
        else:
            lines.append(ln)
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"^(Range|\*RST|Increment|Default unit|Default):\s*(.*)$", ln)
        if m:
            key = {"Range": "range", "*RST": "rst", "Increment": "increment",
                   "Default unit": "unit", "Default": "default"}[m.group(1)]
            extra[key] = m.group(2).strip()
            cur_val = None
            last_key = key
            i += 1
            continue
        if last_key == "range" and cur_val is None and ln not in bold and not ENUM_RE.match(ln):
            # wrapped continuation of a "Range:" line
            extra["range"] = f"{extra['range']} {ln.strip()}"
            i += 1
            continue
        last_key = None
        if re.match(r"^\d+\s*\.\.\s*\d+$", ln) or ln.strip() in ("*", "1 | 2", "1|2"):
            extra["values"] = ln.strip()
            i += 1
            continue
        parsed = parse_enum(ln) if enum is None and ln not in bold else None
        if parsed:
            enum, rest = parsed
            if rest:
                desc.append(rest)
        elif ln in item.get("bold", []) and len(ln.split()) <= 3:
            cur_val = ln.strip()
            value_docs[cur_val] = ""
        elif cur_val is not None:
            value_docs[cur_val] = (value_docs[cur_val] + " " + ln).strip()
        else:
            desc.append(ln)
        i += 1
    if desc:
        out["description"] = norm(" ".join(desc))
    if enum:
        out["enum"] = enum
    if value_docs:
        out["value_docs"] = value_docs
    out.update(extra)
    return out


def parse_suffix_range(s: str | None):
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^(\d+)\s*\.\.\s*(\d+)$", s)
    if m:
        return [int(m.group(1)), int(m.group(2))]
    if "|" in s:
        try:
            vals = [int(v) for v in s.split("|")]
            return [min(vals), max(vals)]
        except ValueError:
            return None
    return None


def fix_brackets(h: str) -> str:
    """Repair manual typos like 'X:CONTrol:ENABle]' -> 'X:CONTrol[:ENABle]'."""
    depth, out = 0, []
    for i, ch in enumerate(h):
        if ch == "[":
            depth += 1
        elif ch == "]":
            if depth == 0:
                j = "".join(out).rfind(":")
                if j < 0:
                    continue
                out.insert(j, "[")
                depth += 1
            depth -= 1
        out.append(ch)
    return "".join(out) + "]" * depth


def fix_case(h: str) -> str:
    """Repair manual typos that print a whole mnemonic in capitals ('FRAME<n>', 'ENABLE').

    Such a mnemonic would have no short form; on FW 03.000 the firmware's short form is
    always the first four letters, so 'FRAME' becomes 'FRAMe'.
    """
    def fix(m: re.Match) -> str:
        w = m.group(0)
        if w.isalpha() and w.isupper() and len(w) > 4:
            return w[:4] + w[4:].lower()
        return w
    return re.sub(r"(?<![*A-Za-z])[A-Za-z]+(?![A-Za-z])", fix, h)


def refine(cmd):
    for h in cmd["headers"]:
        h["header"] = fix_case(fix_brackets(h["header"]))
    usage = " ".join(cmd["usage"]).lower()
    headers = cmd["headers"]
    is_query_hdr = all(h["header"].endswith("?") for h in headers)
    if "event" in usage:
        kind = "event"
    elif "query only" in usage or is_query_hdr:
        kind = "query"
    elif "setting only" in usage:
        kind = "set"
    else:
        kind = "set_query"
    summary = norm(" ".join(cmd["summary_lines"]))
    suffixes = []
    for s in cmd["suffix"]:
        if s["name"] is None and all(v.strip() in (".", "") for v in s["lines"]):
            continue
        r = refine_item(s)
        txt = " ".join(s["lines"])
        r["range"] = parse_suffix_range(r.get("values"))
        if "irrelevant" in txt.lower():
            r["irrelevant"] = True
        suffixes.append(r)
    return {
        "header": headers[0]["header"],
        "aliases": [h["header"] for h in headers[1:]],
        "signature": headers[0]["signature"],
        "kind": kind,
        "summary": summary,
        "section": cmd["section"],
        "section_title": cmd["section_title"],
        "page": cmd["page"],
        "suffixes": suffixes,
        "parameters": [refine_item(p) for p in cmd["parameters"]],
        "query_parameters": [refine_item(p) for p in cmd["query_parameters"]],
        "returns": [refine_item(p) for p in cmd["returns"]],
        "usage": cmd["usage"],
        "examples": cmd["example"],
        "options": cmd["options"],
        "notes": [norm(" ".join(cmd["notes"]))] if cmd["notes"] else [],
    }


# --------------------------------------------------------------------------- check
def canon(h: str) -> str:
    return re.sub(r"[\[\]\s]", "", h).lower()


def completeness(commands, index_entries):
    documented = set()
    for c in commands:
        for h in [c["header"], *c["aliases"]]:
            documented.add(canon(h))
    indexed = {canon(e["command"]): e for e in index_entries}
    missing = sorted(k for k in indexed if k not in documented)
    return {
        "documented_headers": len(documented),
        "index_entries": len(indexed),
        "missing_from_body": [indexed[k] for k in missing],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("-o", "--out", type=Path,
                    default=Path(__file__).resolve().parents[1] / "src/rs_scope_mcp/data/rtb2/commands.json")
    ap.add_argument("--report", type=Path, default=None)
    a = ap.parse_args(argv)

    raw, index_entries = parse(a.pdf)
    commands = [refine(c) for c in raw]
    rep = completeness(commands, index_entries)
    meta = {
        "source": "R&S RTB2 User Manual 1333.1611.02 - 13 (FW 3.xx), chapter 16",
        "generator": "scripts/extract_scpi.py",
        "command_blocks": len(commands),
        "headers_incl_aliases": rep["documented_headers"],
        "index_entries": rep["index_entries"],
        "missing_from_body": len(rep["missing_from_body"]),
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"meta": meta, "commands": commands}, indent=1, ensure_ascii=False))
    if a.report:
        a.report.write_text(json.dumps(rep, indent=1))
    by_kind = defaultdict(int)
    for c in commands:
        by_kind[c["kind"]] += 1
    print(json.dumps(meta, indent=1))
    print("by kind:", dict(by_kind))
    for m in rep["missing_from_body"][:40]:
        print("  MISSING:", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
