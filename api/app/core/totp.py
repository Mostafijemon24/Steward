"""Time-based one-time passwords (RFC 6238, SHA-1, 6 digits, 30 s) — what authenticator apps use.

verify() accepts the current step and one step either side (clock drift) and returns the matched
step, so the caller can store it and refuse the same or an older code again (replay protection).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6


def new_secret() -> str:
    """160-bit random secret, base32 without padding (the format authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret_b32: str) -> bytes:
    pad = "=" * (-len(secret_b32) % 8)
    return base64.b32decode(secret_b32.upper() + pad)


def code_at(secret_b32: str, step: int) -> str:
    digest = hmac.new(_key(secret_b32), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def verify(secret_b32: str, code: str, last_used_step: int | None, now: float | None = None) -> int | None:
    code = "".join(ch for ch in code if not ch.isspace())
    # ASCII digits only ("١٢٣٤٥٦".isdigit() is True, and compare_digest rejects non-ASCII str).
    if len(code) != DIGITS or any(ch not in "0123456789" for ch in code):
        return None
    step = current_step(now)
    matched = None
    for candidate in (step - 1, step, step + 1):
        # Compare every candidate (no early exit) in constant time.
        if hmac.compare_digest(code_at(secret_b32, candidate).encode(), code.encode()):
            matched = candidate
    if matched is None or (last_used_step is not None and matched <= last_used_step):
        return None
    return matched


def provisioning_uri(secret_b32: str, account: str, issuer: str = "Steward") -> str:
    label = quote(f"{issuer}:{account}")
    query = urlencode(
        {"secret": secret_b32, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS, "period": STEP_SECONDS}
    )
    return f"otpauth://totp/{label}?{query}"
