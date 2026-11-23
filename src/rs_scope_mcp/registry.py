"""Layer 1: the complete RTB2 SCPI command registry.

Loads data/<family>/commands.json (generated from the manual by scripts/extract_scpi.py)
and provides:
  * resolve()   - map a concrete command line ("chan2:scal 0.5") to its spec,
                  extracting numeric suffixes and validating them
  * validate()  - check suffix ranges, enum values and numeric ranges
  * search()    - find commands by keyword for discovery
  * describe()  - compact, LLM-friendly description of one command

Validation is deliberately split into errors (unknown header, suffix out of
range, setting a query-only command) and warnings (enum or range mismatch).
The instrument's own error queue stays the final authority.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any

_MNEMONIC_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)(<(\w+)>)?")


def short_form(mnemonic: str) -> str:
    """SCPI short form = the leading uppercase/digit characters ("CHANnel" -> "CHAN")."""
    out = []
    for ch in mnemonic:
        if ch.isupper() or ch.isdigit():
            out.append(ch)
        else:
            break
    return "".join(out) or mnemonic.upper()


def _alt(mnemonic: str) -> str:
    s, long = short_form(mnemonic), mnemonic.upper()
    return re.escape(long) if s == long else f"(?:{re.escape(long)}|{re.escape(s)})"


def header_regex(header: str) -> tuple[re.Pattern, list[str]]:
    """Compile a manual header like 'BUS<b>:UART:FRAMe<n>[:DATA]?' into a regex.

    Returns the pattern and the ordered list of suffix names it captures.
    """
    h = header.strip()
    h = h.removesuffix("?")
    suffixes: list[str] = []
    out = []
    depth = 0
    i = 0
    while i < len(h):
        ch = h[i]
        if ch == "[":
            out.append("(?:")
            depth += 1
            i += 1
        elif ch == "]":
            if depth:  # tolerate stray closing brackets
                out.append(")?")
                depth -= 1
            i += 1
        elif ch == ":":
            out.append(":")
            i += 1
        elif ch == "*":
            out.append(r"\*")
            i += 1
        else:
            m = _MNEMONIC_RE.match(h, i)
            if not m:
                out.append(re.escape(ch))
                i += 1
                continue
            out.append(_alt(m.group(1)))
            if m.group(3):
                out.append(f"(?P<s{len(suffixes)}>\\d+)?")
                suffixes.append(m.group(3))
            i = m.end()
    out.append(")?" * depth)
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE), suffixes


@dataclass
class Command:
    header: str
    aliases: list[str]
    kind: str  # query | set | set_query | event
    summary: str
    section: str
    section_title: str
    page: int
    signature: str
    suffixes: list[dict]
    parameters: list[dict]
    query_parameters: list[dict]
    returns: list[dict]
    usage: list[str]
    examples: list[str]
    raw: dict = field(repr=False, default_factory=dict)
    source: str = "manual"            # manual | firmware (undocumented, from SYSTem:TREE?)
    firmware: set = field(default_factory=set)    # firmware versions whose tree lists it
    absent_in: set = field(default_factory=set)   # firmware versions known NOT to implement it
    notes: list = field(default_factory=list)     # bench-verified behaviour

    @property
    def root(self) -> str:
        return re.split(r"[:<\[?]", self.header.lstrip("*"), maxsplit=1)[0].upper()

    @property
    def queryable(self) -> bool:
        return self.kind in ("query", "set_query", "undocumented") or any(
            h.endswith("?") for h in self.all_headers)

    @property
    def settable(self) -> bool:
        return self.kind in ("set", "set_query", "event", "undocumented")

    @property
    def all_headers(self) -> list[str]:
        return [self.header, *self.aliases]

    def suffix_spec(self, name: str) -> dict | None:
        for s in self.suffixes:
            if s.get("name") == f"<{name}>":
                return s
        return None


@dataclass
class Resolved:
    command: Command
    header_matched: str
    is_query: bool
    suffixes: dict[str, int]
    args: list[str]
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


class Registry:
    def __init__(self, data: dict):
        self.meta = data.get("meta", {})
        self.commands: list[Command] = []
        self._compiled: list[tuple[re.Pattern, list[str], Command, str]] = []
        for c in data["commands"]:
            cmd = Command(
                header=c["header"], aliases=c.get("aliases", []), kind=c["kind"],
                summary=c.get("summary", ""), section=c.get("section", ""),
                section_title=c.get("section_title", ""), page=c.get("page", 0),
                signature=c.get("signature", ""), suffixes=c.get("suffixes", []),
                parameters=c.get("parameters", []), query_parameters=c.get("query_parameters", []),
                returns=c.get("returns", []), usage=c.get("usage", []),
                examples=c.get("examples", []), raw=c,
            )
            self.commands.append(cmd)
            for h in cmd.all_headers:
                rx, names = header_regex(h)
                self._compiled.append((rx, names, cmd, h))

    # ------------------------------------------------------------------ loading
    def _add(self, cmd: Command) -> None:
        self.commands.append(cmd)
        for h in cmd.all_headers:
            rx, names = header_regex(h)
            self._compiled.append((rx, names, cmd, h))

    @classmethod
    @lru_cache(maxsize=4)
    def default(cls, family: str = "rtb2") -> Registry:
        """Registry for an instrument family (data/<family>/: commands.json, firmware/*.tree,
        overrides.json). 'rtb2' covers the R&S RTB2 and RTB2000 series (same hardware and
        interchangeable firmware)."""
        data = resources.files(f"rs_scope_mcp.data.{family}")
        reg = cls(json.loads(data.joinpath("commands.json").read_text(encoding="utf-8")))
        fw_dir = data.joinpath("firmware")
        for f in sorted(fw_dir.iterdir(), key=lambda p: p.name):
            if f.name.endswith(".tree"):
                version = f.name.rsplit(".tree", 1)[0].split("-", 1)[-1]
                reg.apply_firmware_tree(f.read_text(encoding="utf-8").split(), version)
        ov = data.joinpath("overrides.json")
        if ov.is_file():
            reg.apply_overrides(json.loads(ov.read_text(encoding="utf-8")))
        return reg

    # ------------------------------------------------------------------ overlays
    def apply_firmware_tree(self, headers: list[str], version: str) -> dict:
        """Overlay the instrument's own command list (SYSTem:TREE?, ';' or whitespace separated).

        Marks documented commands as present in this firmware and adds every header the
        manual does not document as an 'undocumented' command, so validation never blocks
        a command the firmware actually implements.
        """
        hs = [h.strip().lstrip(":") for h in headers if h.strip() and not h.strip().startswith("&")]
        matched = 0
        unmatched: list[str] = []
        for h in hs:
            hits = self.match_header(h)
            if hits:
                matched += 1
                for cmd, _, _ in hits:
                    cmd.firmware.add(version)
            else:
                unmatched.append(h)
        # generalise numeric suffixes: CHAN2:HIST:ATIC -> CHAN<a>:HIST:ATIC (suffix optional)
        groups: dict[str, dict[str, set[int]]] = {}
        order: list[str] = []
        for h in unmatched:
            toks, names, sfx = [], [], {}
            for k, tok in enumerate(h.split(":")):
                m = re.match(r"^(.*?[A-Z])(\d+)$", tok)
                if m and not tok.startswith("*"):
                    name = "abcdefgh"[len(names)]
                    names.append(name)
                    toks.append(f"{m.group(1)}<{name}>")
                    sfx[name] = int(m.group(2))
                else:
                    toks.append(tok)
            key = ":".join(toks)
            if key not in groups:
                groups[key] = {n: set() for n in names}
                order.append(key)
            for n, v in sfx.items():
                groups[key][n].add(v)
        added = 0
        for key in order:
            # plain variant (no digits) may already be covered by a generalised one
            if "<" not in key and self.match_header(key):
                continue
            suffixes = [{"name": f"<{n}>", "values": f"{min(v)}..{max(v)}" if v else "",
                         "range": [min(min(v), 1), max(v)] if v else None}
                        for n, v in groups[key].items()]
            self._add(Command(
                header=key, aliases=[], kind="undocumented",
                summary=f"Not in the user manual; listed by firmware {version} (SYSTem:TREE?).",
                section="fw", section_title="Undocumented firmware command", page=0,
                signature="", suffixes=suffixes, parameters=[], query_parameters=[], returns=[],
                usage=[], examples=[], source="firmware", firmware={version}))
            added += 1
        self.meta.setdefault("firmware_trees", {})[version] = {
            "headers": len(hs), "matched_manual": matched, "undocumented_added": added}
        return self.meta["firmware_trees"][version]

    def apply_overrides(self, overrides: list[dict]) -> None:
        """Bench-verified corrections.

        Entry keys: match (regex on headers), enum_add {param_index: [values]}, notes,
        absent_in [firmware versions], replace {header, aliases, kind, parameters, summary}
        to restructure a mis-documented block, and add_command {...} for a command the manual
        merged into another block.
        """
        for ov in overrides:
            if "add_command" in ov:
                c = ov["add_command"]
                self.commands.append(Command(
                    header=c["header"], aliases=c.get("aliases", []), kind=c.get("kind", "set_query"),
                    summary=c.get("summary", ""), section=c.get("section", ""),
                    section_title=c.get("section_title", ""), page=c.get("page", 0),
                    signature=c.get("signature", ""), suffixes=c.get("suffixes", []),
                    parameters=c.get("parameters", []), query_parameters=[], returns=[], usage=[],
                    examples=[], source="bench", notes=list(c.get("notes", [])),
                    firmware=set(c.get("firmware", []))))
                continue
            rx = re.compile(ov["match"])
            for cmd in self.commands:
                if not any(rx.search(h) for h in cmd.all_headers):
                    continue
                for key, val in (ov.get("replace") or {}).items():
                    setattr(cmd, key, val)
                for idx, vals in (ov.get("enum_add") or {}).items():
                    i = int(idx)
                    if i < len(cmd.parameters):
                        enum = cmd.parameters[i].setdefault("enum", [])
                        enum.extend(v for v in vals if v not in enum)
                cmd.notes.extend(n for n in ov.get("notes", []) if n not in cmd.notes)
                cmd.absent_in.update(ov.get("absent_in", []))
        # drop undocumented firmware entries now covered by a documented or bench command
        def sample(h: str) -> str:
            return re.sub(r"<\w+>", "1", h).replace("[", "").replace("]", "").rstrip("?")
        known = [header_regex(h)[0] for c in self.commands if c.source != "firmware" for h in c.all_headers]
        self.commands = [c for c in self.commands if c.source != "firmware"
                         or not any(rx.match(sample(c.header)) for rx in known)]
        # headers may have changed: rebuild the matchers
        self._compiled = []
        for cmd in self.commands:
            for h in cmd.all_headers:
                rx, names = header_regex(h)
                self._compiled.append((rx, names, cmd, h))

    def __len__(self) -> int:
        return len(self.commands)

    # ------------------------------------------------------------------ resolve
    @staticmethod
    def split_line(line: str) -> tuple[str, list[str]]:
        line = line.strip()
        if " " in line:
            head, rest = line.split(None, 1)
        else:
            head, rest = line, ""
        return head, split_args(rest)

    def match_header(self, head: str) -> list[tuple[Command, str, dict[str, int]]]:
        h = head.strip().lstrip(":")
        h = h.removesuffix("?")
        hits = []
        seen = set()
        for rx, names, cmd, hdr in self._compiled:
            m = rx.match(h)
            if not m:
                continue
            key = (id(cmd), hdr)
            if key in seen:
                continue
            seen.add(key)
            sfx = {}
            for i, n in enumerate(names):
                v = m.group(f"s{i}")
                if v is not None:
                    sfx[n] = int(v)
            hits.append((cmd, hdr, sfx))
        return hits

    def resolve(self, line: str) -> Resolved | None:
        head, args = self.split_line(line)
        is_query = head.endswith("?")
        hits = self.match_header(head)
        if not hits:
            return None
        # prefer a header whose query-ness matches the request
        # prefer documented/bench commands over undocumented firmware entries, then query-ness
        hits.sort(key=lambda t: (t[0].source == "firmware", t[1].endswith("?") != is_query, len(t[1])))
        cmd, hdr, sfx = hits[0]
        res = Resolved(cmd, hdr, is_query, sfx, args, [], [])
        self._validate(res)
        return res

    # ------------------------------------------------------------------ validate
    def _validate(self, r: Resolved) -> None:
        c = r.command
        if c.kind == "undocumented":
            r.warnings.append("undocumented command (from the firmware command tree): "
                              "parameters are not validated")
            return
        if r.is_query and not c.queryable:
            r.errors.append(f"{c.header} is {c.kind.replace('_', '/')}; it cannot be queried")
        if not r.is_query and c.kind == "query":
            r.errors.append(f"{c.header} is query-only; append '?'")
        for name, val in r.suffixes.items():
            spec = c.suffix_spec(name)
            if not spec or spec.get("irrelevant"):
                continue
            rng = spec.get("range")
            if rng and not (rng[0] <= val <= rng[1]):
                r.errors.append(f"suffix <{name}>={val} outside {rng[0]}..{rng[1]}")
        params = c.query_parameters if r.is_query else c.parameters
        if r.is_query and not params:
            return
        if not r.is_query and c.kind != "event" and params and not r.args:
            r.errors.append(f"{c.header} needs a value: {c.signature or params[0].get('name')}")
            return
        for i, arg in enumerate(r.args):
            if i >= len(params):
                break
            p = params[i]
            check_value(p, arg, r.warnings)

    # ------------------------------------------------------------------ discovery
    def search(self, query: str, limit: int = 20) -> list[Command]:
        terms = [t.lower() for t in re.findall(r"[\w\-]+", query)]
        if not terms:
            return []
        scored = []
        for c in self.commands:
            hay_h = " ".join(c.all_headers).lower()
            hay = f"{hay_h} {c.summary} {c.section_title}".lower()
            score = 0
            for t in terms:
                if t in hay_h:
                    score += 3
                elif t in hay:
                    score += 1
                else:
                    score -= 1
            if score > 0:
                scored.append((score, c.page, c))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [c for _, _, c in scored[:limit]]

    def by_header(self, header: str) -> Command | None:
        for c in self.commands:
            if header in c.all_headers:
                return c
        hits = self.match_header(header)
        return hits[0][0] if hits else None

    def subsystems(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.commands:
            out[c.root] = out.get(c.root, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# ---------------------------------------------------------------------- helpers
def split_args(s: str) -> list[str]:
    """Split a SCPI argument list on commas, respecting quoted strings and blocks."""
    out, cur, q = [], [], None
    for ch in s:
        if q:
            cur.append(ch)
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
            cur.append(ch)
        elif ch == ",":
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur or out:
        out.append("".join(cur).strip())
    return [a for a in out if a != ""] if out != [""] else []


_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")
_BASE_UNITS = ("HZ", "V", "S", "A", "W", "DB", "DIV", "")
# SCPI is case-insensitive, so "M" means milli and "MA" means mega; MHZ is the classic exception.
_PREFIXES = (("MHZ", 1e6, "HZ"), ("MA", 1e6, None), ("G", 1e9, None), ("K", 1e3, None),
             ("M", 1e-3, None), ("U", 1e-6, None), ("N", 1e-9, None), ("P", 1e-12, None))


def parse_number(s: str) -> float | None:
    """Parse a SCPI numeric value with optional SI unit suffix ('5mV', '10 MHz', '2us')."""
    s = s.strip()
    m = _NUM_RE.match(s)
    if not m:
        return None
    val = float(m.group(0))
    unit = s[m.end():].strip().upper()
    if unit in _BASE_UNITS:
        return val
    for pfx, mult, fixed_base in _PREFIXES:
        if unit.startswith(pfx):
            base = unit[len(pfx):] if fixed_base is None else ""
            if base in _BASE_UNITS:
                return val * mult
    return val


def enum_match(value: str, enum: list[str]) -> str | None:
    v = value.strip().strip("'\"").upper()
    for e in enum:
        if v in (e.upper(), short_form(e).upper()):
            return e
    return None


_RANGE_RE = re.compile(r"^\s*([+-]?[\d.]+(?:[eE][+-]?\d+)?)\s+to\s+([+-]?[\d.]+(?:[eE][+-]?\d+)?)")


def check_value(param: dict, arg: str, warnings: list[str]) -> None:
    name = param.get("name") or "value"
    enum = param.get("enum")
    num = parse_number(arg)
    if enum:
        if enum_match(arg, enum):
            return
        if {"ON", "OFF"} <= {e.upper() for e in enum} and arg.strip() in ("0", "1"):
            return
        if num is None or "range" not in param:
            warnings.append(f"{name}={arg!r} not in documented values {enum}")
            return
    rng = param.get("range")
    if rng and num is not None:
        m = _RANGE_RE.match(rng)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            if not (lo <= num <= hi) and "probe" not in rng.lower():
                warnings.append(f"{name}={arg} outside documented range {rng}")


def describe(cmd: Command, verbose: bool = False) -> dict[str, Any]:
    """Compact, LLM-friendly view of one command."""
    d: dict[str, Any] = {
        "header": cmd.header,
        "kind": cmd.kind,
        "summary": cmd.summary,
        "section": f"{cmd.section} {cmd.section_title}".strip(),
        "manual_page": cmd.page,
    }
    if cmd.signature:
        d["signature"] = cmd.signature
    if cmd.aliases:
        d["aliases"] = cmd.aliases
    if cmd.suffixes:
        d["suffixes"] = {s["name"]: (s.get("values") or s.get("description", "")) for s in cmd.suffixes}

    def slim(p: dict) -> dict:
        keep = {k: p[k] for k in ("name", "enum", "range", "rst", "unit", "increment") if k in p}
        if verbose:
            for k in ("description", "value_docs"):
                if k in p:
                    keep[k] = p[k]
        elif "description" in p and "enum" not in p:
            keep["description"] = p["description"][:200]
        return keep

    if cmd.parameters:
        d["parameters"] = [slim(p) for p in cmd.parameters]
    if cmd.query_parameters:
        d["query_parameters"] = [slim(p) for p in cmd.query_parameters]
    if cmd.returns:
        d["returns"] = [slim(p) for p in cmd.returns]
    if verbose and cmd.examples:
        d["examples"] = cmd.examples
    if cmd.source != "manual":
        d["source"] = cmd.source
    if cmd.firmware:
        d["firmware_tree"] = sorted(cmd.firmware)
    if cmd.absent_in:
        d["not_implemented_in_firmware"] = sorted(cmd.absent_in)
    if cmd.notes:
        d["notes"] = cmd.notes
    return d
