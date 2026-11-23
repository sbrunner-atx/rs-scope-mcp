"""Layer 2: the instrument session.

Wraps the transport with everything a reliable remote session needs:
  * identification and installed options (*IDN?, *OPT?)
  * registry validation of every command before it is sent
  * the error queue drained after writes, so failures are never silent
  * *OPC? synchronisation for long operations
  * batched queries (many queries, one round trip)
  * a safety gate for commands that change the world outside the screen:
    generator outputs, resets, file deletion, network settings, firmware
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .registry import Registry, Resolved
from .transport import SocketTransport, TransportError


class ValidationError(Exception):
    pass


class GuardedCommand(Exception):
    """Raised when a guarded command is sent without confirmation."""


class InstrumentError(Exception):
    """The instrument rejected a command (error queue entries attached)."""

    def __init__(self, line: str, errors: list[str]):
        self.line = line
        self.errors = errors
        super().__init__(f"{line}: {', '.join(errors) or 'no response'}")


@dataclass
class Guard:
    pattern: re.Pattern
    reason: str
    only_when: re.Pattern | None = None  # argument pattern that triggers the guard


def _g(rx: str, reason: str, when: str | None = None) -> Guard:
    return Guard(re.compile(rx, re.IGNORECASE), reason, re.compile(when, re.IGNORECASE) if when else None)


# Matched against the *manual header* the line resolved to (not the raw text),
# so short and long forms are covered alike.
GUARDS: list[Guard] = [
    _g(r"^\*RST$|^SYSTem:PRESet$", "resets every instrument setting"),
    _g(r"^\*CAL\?$|^CALibration$", "runs self-alignment (several minutes, inputs must be disconnected)"),
    _g(r"^WGENerator:OUTPut\[:ENABle\]$", "drives the generator output into the circuit", r"^(ON|1)$"),
    _g(r"^PGENerator:PATTern:STATe$", "drives the pattern generator pins into the circuit", r"^(ON|1)$"),
    _g(r"^PGENerator:MANual:STATe<s>$", "drives a pattern generator pin into the circuit"),
    _g(r"^BPLot:(STATe|ENABle)$", "a Bode sweep drives the generator output into the circuit", r"^(ON|RUN|1)$"),
    _g(r"^MMEMory:(DELete|RDIRectory|MOVE)$", "deletes or moves files on the instrument"),
    _g(r"^MMEMory:DATA$", "overwrites a file on the instrument"),
    _g(r"^SYSTem:COMMunicate:", "changes interface/network settings and can drop this connection"),
    _g(r"^DIAGnostic:UPDate", "firmware update"),
    _g(r"^SYSTem:EDUCation", "education-mode lock settings"),
    _g(r"^SYSTem:(DATE|TIME|NAME)$", "changes system identity/clock"),
]


MODE_CHANGES = {"DEVice:MODE", "SPECtrum[:STATe]", "BPLot:ENABle", "MASK:STATe", "DISPlay:MODE"}


@dataclass
class CommandResult:
    sent: str
    response: str | None = None
    header: str | None = None
    warnings: list[str] = field(default_factory=list)
    instrument_errors: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.instrument_errors

    def as_dict(self) -> dict:
        d = {"sent": self.sent, "ok": self.ok, "elapsed_ms": round(self.elapsed_ms, 1)}
        if self.header:
            d["command"] = self.header
        if self.response is not None:
            d["response"] = self.response
        if self.warnings:
            d["warnings"] = self.warnings
        if self.instrument_errors:
            d["instrument_errors"] = self.instrument_errors
        return d


class Instrument:
    def __init__(self, host: str, port: int = 5025, timeout: float = 10.0,
                 registry: Registry | None = None, validate: bool = True):
        self.transport = SocketTransport(host, port, timeout)
        self.registry = registry or Registry.default()
        self.validate = validate
        self.idn: dict[str, str] = {}
        self.options: list[str] = []
        self.batch_size = 16

    # ------------------------------------------------------------------ session
    def connect(self) -> dict:
        self.transport.connect()
        raw = self.transport.query("*IDN?")
        parts = [p.strip() for p in raw.split(",")]
        keys = ["manufacturer", "model", "serial", "firmware"]
        self.idn = dict(zip(keys, parts + [""] * (4 - len(parts))))
        self.idn["raw"] = raw
        opt = self.transport.query("*OPT?").strip()
        self.options = [o.strip() for o in opt.split(",") if o.strip() and o.strip() not in ("0", '""')]
        self.drain_errors()
        return {"idn": self.idn, "options": self.options}

    def close(self) -> None:
        self.transport.close()

    @property
    def connected(self) -> bool:
        return self.transport.connected

    def ensure(self) -> None:
        if not self.transport.connected:
            self.connect()

    # ------------------------------------------------------------------ checks
    def check(self, line: str) -> Resolved | None:
        r = self.registry.resolve(line)
        if r is None:
            if self.validate:
                raise ValidationError(f"unknown command header: {line.split()[0]!r} "
                                      "(use scpi_search to find the right one)")
            return None
        if r.errors and self.validate:
            raise ValidationError("; ".join(r.errors))
        fw = self.idn.get("firmware")
        if self.validate and fw and fw in r.command.absent_in:
            raise ValidationError(f"{r.command.header} is not implemented in firmware {fw} "
                                  f"({'; '.join(r.command.notes) or 'bench-verified'})")
        return r

    def guard_reason(self, r: Resolved | None, line: str) -> str | None:
        if r is None:
            return None
        hdr = r.header_matched
        if r.is_query and not hdr.endswith("?"):
            hdr = hdr + "?"
        for g in GUARDS:
            if g.pattern.search(hdr):
                if r.is_query and not hdr.startswith("*CAL"):
                    return None
                if g.only_when is not None and (
                        not r.args or not g.only_when.match(r.args[0].strip())):
                    return None
                return g.reason
        return None

    # ------------------------------------------------------------------ errors / sync
    def drain_errors(self, limit: int = 50) -> list[str]:
        errs = []
        for _ in range(limit):
            e = self.transport.query("SYSTem:ERRor?").strip()
            if not e or e.startswith(("0,", "+0,")):
                break
            errs.append(e)
        return errs

    def opc(self, timeout: float = 30.0) -> None:
        resp = self.transport.query("*OPC?", timeout=timeout).strip()
        if resp not in ("1", "+1"):
            raise TransportError(f"unexpected *OPC? response {resp!r}")

    # ------------------------------------------------------------------ I/O
    def write(self, line: str, confirm: bool = False, check_errors: bool = True,
              wait: bool = False, timeout: float = 30.0) -> CommandResult:
        self.ensure()
        t0 = time.monotonic()
        r = self.check(line)
        if r and r.is_query:
            raise ValidationError("write() got a query; use query()")
        reason = self.guard_reason(r, line)
        if reason and not confirm:
            raise GuardedCommand(f"{line!r} is guarded: {reason}. Re-send with confirm=true.")
        self.transport.write(line)
        # application changes take ~1 s; commands sent meanwhile fail with -221 (bench-verified)
        if wait or (r is not None and r.command.header in MODE_CHANGES):
            self.opc(timeout)
        # The Bode application changes memory (LIMited, 75 kSa, and ACQ:MEM AUT is then refused with
        # -221), channel scale and coupling, and the generator load. Leaving it restores the complete
        # setup saved before the sweep (bench-verified as the only way back to AUTO memory).
        if (r is not None and r.command.header == "DEVice:MODE" and r.args
                and r.args[0].strip().upper() != "BPL" and getattr(self, "setup_before_bode", None)):
            from .transport import encode_block
            blob, self.setup_before_bode = self.setup_before_bode, None
            self.transport.write("WGENerator:OUTPut OFF")
            self.transport.write_raw(b"SYSTem:SET " + encode_block(blob) + b"\n")
            self.opc(30)
            self.drain_errors()
        res = CommandResult(sent=line, header=r.command.header if r else None,
                            warnings=r.warnings if r else [])
        if check_errors:
            res.instrument_errors = self.drain_errors()
        res.elapsed_ms = (time.monotonic() - t0) * 1000
        return res

    def query(self, line: str, timeout: float | None = None, check_errors: bool = False,
              confirm: bool = False) -> CommandResult:
        self.ensure()
        t0 = time.monotonic()
        if not line.split()[0].endswith("?"):
            raise ValidationError("query() needs a header ending in '?'")
        r = self.check(line)
        reason = self.guard_reason(r, line)
        if reason and not confirm:
            raise GuardedCommand(f"{line!r} is guarded: {reason}. Re-send with confirm=true.")
        resp = self._sentinel_query(line, timeout)
        res = CommandResult(sent=line, response=resp, header=r.command.header if r else None,
                            warnings=r.warnings if r else [])
        if check_errors:
            res.instrument_errors = self.drain_errors()
        res.elapsed_ms = (time.monotonic() - t0) * 1000
        return res

    def query_block(self, line: str, timeout: float | None = None) -> bytes:
        self.ensure()
        self.check(line)
        try:
            return self.transport.query_block(line, timeout=timeout)
        except TransportError:
            errs = self.drain_errors() if self.transport.connected else []
            if errs:
                raise TransportError(f"{line}: no block data; instrument says {errs}") from None
            raise

    def _sentinel_query(self, line: str, timeout: float | None = None) -> str:
        """Query with a trailing *OPC? sentinel.

        The RTB2 answers nothing at all to a query it rejects (undefined header,
        wrong bus type, ...). Without a sentinel that costs a full timeout; with
        "X?;*OPC?" the scope still answers "1", so a lone "1" means X? failed and the
        reason is read from the error queue immediately.
        """
        if line.strip().upper() in ("*OPC?",):
            return self.transport.query(line, timeout=timeout)
        resp = self.transport.query(f"{line};*OPC?", timeout=timeout)
        parts = split_responses(resp)
        if len(parts) >= 2 and parts[-1] in ("1", "+1"):
            return ";".join(parts[:-1])
        if parts == ["1"] or parts == ["+1"]:
            raise InstrumentError(line, self.drain_errors())
        raise TransportError(f"{line}: unexpected response framing {resp[:80]!r}")

    def query_many(self, lines: list[str], timeout: float | None = None) -> list[str | None]:
        """Run many ASCII queries with few round trips ("A?;:B?;:C?;*OPC?").

        Returns one entry per query; a query the scope rejected yields None and its
        error is recorded in self.last_errors[index].
        """
        self.ensure()
        for ln in lines:
            if not ln.split()[0].endswith("?"):
                raise ValidationError(f"query_many only takes queries: {ln!r}")
            self.check(ln)
        self.last_errors: dict[int, list[str]] = {}
        out: list[str | None] = []
        for i in range(0, len(lines), self.batch_size):
            chunk = lines[i : i + self.batch_size]
            joined = ";".join(":" + ln.lstrip(":") if not ln.startswith("*") else ln for ln in chunk)
            resp = self.transport.query(joined + ";*OPC?", timeout=timeout)
            parts = split_responses(resp)
            if parts and parts[-1] in ("1", "+1") and len(parts) - 1 == len(chunk):
                out.extend(parts[:-1])
                continue
            # something was rejected: clear the queue and redo one by one to locate it
            self.drain_errors()
            for j, ln in enumerate(chunk):
                try:
                    out.append(self._sentinel_query(ln, timeout))
                except InstrumentError as e:
                    out.append(None)
                    self.last_errors[i + j] = e.errors
        return out


def split_responses(resp: str) -> list[str]:
    """Split a ';'-joined multi-query response, respecting quoted strings."""
    parts, cur, q = [], [], False
    for ch in resp:
        if ch == '"':
            q = not q
        if ch == ";" and not q:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts]
