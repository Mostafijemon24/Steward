"""Malware scanning through a ClamAV daemon (clamd) using its INSTREAM protocol over TCP.

No extra dependency: the protocol is "zINSTREAM\\0", then chunks prefixed with a 4-byte
big-endian length, then a zero-length chunk; clamd answers "stream: OK\\0" or
"stream: <Signature> FOUND\\0" (or an error line).

Fail closed: any error talking to clamd raises ScannerUnavailable, and the upload is refused.
"""

from __future__ import annotations

import socket
import struct
from collections.abc import Callable, Iterable

from app.domain.enums import ScanStatus

CHUNK = 64 * 1024
MAX_REPLY = 4096


class ScannerUnavailable(Exception):
    """clamd could not be reached or did not give a usable verdict."""


class ClamdScanner:
    def __init__(self, host: str, port: int, timeout: float, read_chunks: Callable[[str], Iterable[bytes]]) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        # Callable key -> Iterable[bytes]; injected so the scanner never touches paths itself.
        self._read = read_chunks
        self.last_signature: str | None = None

    def is_configured(self) -> bool:
        return True

    def _connect(self) -> socket.socket:
        try:
            return socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise ScannerUnavailable(f"cannot reach the malware scanner ({type(e).__name__})") from None

    @staticmethod
    def _reply(sock: socket.socket) -> str:
        buf = b""
        while not buf.endswith(b"\0") and len(buf) < MAX_REPLY:
            part = sock.recv(1024)
            if not part:
                break
            buf += part
        return buf.rstrip(b"\0").decode("utf-8", "replace").strip()

    def ping(self) -> bool:
        try:
            with self._connect() as s:
                s.sendall(b"zPING\0")
                return self._reply(s) == "PONG"
        except (OSError, ScannerUnavailable):
            return False

    def scan_chunks(self, chunks: Iterable[bytes]) -> str:
        """Return ScanStatus.clean or ScanStatus.infected; raise ScannerUnavailable otherwise."""
        self.last_signature = None
        try:
            with self._connect() as s:
                s.sendall(b"zINSTREAM\0")
                for chunk in chunks:
                    for i in range(0, len(chunk), CHUNK):
                        piece = chunk[i : i + CHUNK]
                        s.sendall(struct.pack(">I", len(piece)) + piece)
                s.sendall(struct.pack(">I", 0))
                reply = self._reply(s)
        except ScannerUnavailable:
            raise
        except OSError as e:
            raise ScannerUnavailable(f"malware scan failed ({type(e).__name__})") from None
        if reply.endswith("OK") and "FOUND" not in reply:
            return ScanStatus.clean.value
        if reply.endswith("FOUND"):
            # "stream: Win.Test.EICAR_HDB-1 FOUND" -> keep only the signature name (no file data).
            body = reply.split(":", 1)[-1].strip()
            self.last_signature = body.removesuffix("FOUND").strip()[:120] or "unknown"
            return ScanStatus.infected.value
        # e.g. "INSTREAM size limit exceeded. ERROR"
        raise ScannerUnavailable("the malware scanner returned an error")

    def scan(self, key: str) -> str:
        return self.scan_chunks(self._read(key))
