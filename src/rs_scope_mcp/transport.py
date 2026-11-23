"""Layer 0: raw SCPI over TCP (the RTB2 "IP Port", default 5025).

No VISA dependency. Responses are newline terminated; binary payloads use
IEEE 488.2 definite-length block data:  #<n><len><bytes>  followed by "\\n".
"""
from __future__ import annotations

import socket
import threading
import time


class TransportError(Exception):
    """Connection or framing failure (not an instrument SCPI error)."""


class SocketTransport:
    def __init__(self, host: str, port: int = 5025, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = bytearray()
        self.lock = threading.RLock()

    # ------------------------------------------------------------------ lifecycle
    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        with self.lock:
            if self._sock:
                return
            try:
                s = socket.create_connection((self.host, self.port), timeout=self.timeout)
            except OSError as e:
                raise TransportError(f"cannot connect to {self.host}:{self.port}: {e}") from e
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(self.timeout)
            self._sock = s
            self._buf.clear()

    def close(self) -> None:
        with self.lock:
            if self._sock:
                try:
                    self._sock.close()
                finally:
                    self._sock = None
                    self._buf.clear()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ primitives
    def _require(self) -> socket.socket:
        if not self._sock:
            raise TransportError("not connected")
        return self._sock

    def write(self, line: str) -> None:
        data = line.rstrip("\n").encode("ascii", errors="strict") + b"\n"
        with self.lock:
            try:
                self._require().sendall(data)
            except OSError as e:
                self.close()
                raise TransportError(f"write failed: {e}") from e

    def write_raw(self, data: bytes) -> None:
        with self.lock:
            try:
                self._require().sendall(data)
            except OSError as e:
                self.close()
                raise TransportError(f"write failed: {e}") from e

    def _fill(self, timeout: float | None) -> None:
        sock = self._require()
        if timeout is not None:
            sock.settimeout(timeout)
        try:
            chunk = sock.recv(1 << 16)
        except TimeoutError as e:
            raise TransportError("timeout waiting for instrument response") from e
        except OSError as e:
            self.close()
            raise TransportError(f"read failed: {e}") from e
        finally:
            if timeout is not None and self._sock:
                self._sock.settimeout(self.timeout)
        if not chunk:
            self.close()
            raise TransportError("connection closed by instrument")
        self._buf.extend(chunk)

    def _read_exact(self, n: int, timeout: float | None) -> bytes:
        deadline = time.monotonic() + (timeout or self.timeout)
        while len(self._buf) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportError(f"timeout reading {n} bytes (got {len(self._buf)})")
            self._fill(remaining)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def read_line(self, timeout: float | None = None) -> str:
        with self.lock:
            deadline = time.monotonic() + (timeout or self.timeout)
            while True:
                i = self._buf.find(b"\n")
                if i >= 0:
                    line = bytes(self._buf[:i])
                    del self._buf[: i + 1]
                    return line.decode("latin-1").rstrip("\r")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportError("timeout waiting for response line")
                self._fill(remaining)

    def read_block(self, timeout: float | None = None) -> bytes:
        """Read one IEEE 488.2 block (#<n><len><data>) and its trailing newline."""
        with self.lock:
            head = self._read_exact(2, timeout)
            if head[:1] != b"#":
                # not a block: return the rest of the line as an error context
                rest = head + self.read_line(timeout).encode("latin-1")
                raise TransportError(f"expected block data, got {rest[:80]!r}")
            ndig = int(head[1:2])
            if ndig == 0:
                # indefinite length: terminated by newline (rare on sockets)
                return self.read_line(timeout).encode("latin-1")
            length = int(self._read_exact(ndig, timeout))
            data = self._read_exact(length, timeout)
            # consume the terminating newline if present
            try:
                if not self._buf:
                    self._fill(0.2)
                if self._buf[:1] == b"\n":
                    del self._buf[:1]
            except TransportError:
                pass
            return data

    # ------------------------------------------------------------------ convenience
    def query(self, line: str, timeout: float | None = None) -> str:
        with self.lock:
            self.write(line)
            return self.read_line(timeout)

    def query_block(self, line: str, timeout: float | None = None) -> bytes:
        with self.lock:
            self.write(line)
            return self.read_block(timeout)


def encode_block(data: bytes) -> bytes:
    """Encode bytes as IEEE 488.2 definite-length block data."""
    n = str(len(data))
    return b"#" + str(len(n)).encode() + n.encode() + data


def parse_block(buf: bytes) -> tuple[bytes, int]:
    """Parse a block from the start of buf; return (payload, bytes_consumed)."""
    if not buf.startswith(b"#"):
        raise ValueError("not block data")
    ndig = int(buf[1:2])
    length = int(buf[2 : 2 + ndig])
    start = 2 + ndig
    return buf[start : start + length], start + length
