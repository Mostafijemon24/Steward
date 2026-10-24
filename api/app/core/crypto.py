"""Encryption at rest for small secrets (MFA seeds, integration credentials).

AES-256-GCM with a random 96-bit nonce. Every value is bound to its purpose and owner through
the associated data (e.g. "mfa:<user id>"), so a ciphertext copied into another row or used
for another purpose fails to decrypt. The key lives only in the environment
(SECRETS_ENCRYPTION_KEY, base64 of 32 bytes), never in the database or in backups.

Rotation: put the new key in SECRETS_ENCRYPTION_KEY and the old one in
SECRETS_ENCRYPTION_PREVIOUS_KEY. Values are decrypted with either and re-sealed with the new key
when they are next written.

Integration credentials use a separate, asymmetric scheme (seal_for_worker / unseal_on_worker):
an ephemeral X25519 key agreement with the tools worker's public key, HKDF-SHA256 (bound to the
purpose), then AES-256-GCM. The API holds only the public key, so even code execution in the API
cannot decrypt stored WordPress credentials; only the tools worker has the private key.

Generate keys (writes nothing to disk; redirect into your .env):  python -m app.core.crypto keygen
"""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import DEV_INTEGRATION_PRIVATE_KEY, DEV_SECRETS_KEY, get_settings

PREFIX = "v1:"


class DecryptionError(Exception):
    pass


def parse_key(value: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("SECRETS_ENCRYPTION_KEY must be base64 (generate: openssl rand -base64 32)") from None
    if len(raw) != 32:
        raise ValueError("SECRETS_ENCRYPTION_KEY must decode to exactly 32 bytes (openssl rand -base64 32)")
    return raw


def _keys() -> list[bytes]:
    s = get_settings()
    current = s.secrets_encryption_key.get_secret_value()
    if s.is_production and current == DEV_SECRETS_KEY:
        raise DecryptionError("SECRETS_ENCRYPTION_KEY is not configured for this process")
    keys = [parse_key(current)]
    if s.secrets_encryption_previous_key is not None:
        keys.append(parse_key(s.secrets_encryption_previous_key.get_secret_value()))
    return keys


def seal(plaintext: bytes, purpose: str) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_keys()[0]).encrypt(nonce, plaintext, purpose.encode("utf-8"))
    return PREFIX + base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def unseal(token: str, purpose: str) -> bytes:
    if not token.startswith(PREFIX):
        raise DecryptionError("unknown secret format")
    try:
        blob = base64.urlsafe_b64decode(token[len(PREFIX) :])
    except (binascii.Error, ValueError):
        raise DecryptionError("corrupt secret") from None
    nonce, ct = blob[:12], blob[12:]
    for key in _keys():
        try:
            return AESGCM(key).decrypt(nonce, ct, purpose.encode("utf-8"))
        except InvalidTag:
            continue
    raise DecryptionError("secret cannot be decrypted with the configured key(s)")


# ---------------------------------------------------------------- integration credentials (asymmetric)
WORKER_PREFIX = "x1:"


def _raw32(value: str, name: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(f"{name} must be base64") from None
    if len(raw) != 32:
        raise ValueError(f"{name} must decode to 32 bytes")
    return raw


def _kdf(shared: bytes, eph_pub: bytes, purpose: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=eph_pub, info=b"steward-x1:" + purpose.encode()).derive(
        shared
    )


def _pub_bytes(key: X25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def seal_for_worker(plaintext: bytes, purpose: str) -> str:
    """Encrypt to the tools worker's public key (the caller cannot decrypt the result)."""
    recipient = X25519PublicKey.from_public_bytes(
        _raw32(get_settings().integration_secrets_public_key, "INTEGRATION_SECRETS_PUBLIC_KEY")
    )
    eph = X25519PrivateKey.generate()
    eph_pub = _pub_bytes(eph.public_key())
    key = _kdf(eph.exchange(recipient), eph_pub, purpose)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, purpose.encode())
    return WORKER_PREFIX + base64.urlsafe_b64encode(eph_pub + nonce + ct).decode("ascii")


def unseal_on_worker(token: str, purpose: str) -> bytes:
    s = get_settings()
    priv_b64 = s.integration_secrets_private_key.get_secret_value()
    if s.is_production and priv_b64 == DEV_INTEGRATION_PRIVATE_KEY:
        raise DecryptionError("INTEGRATION_SECRETS_PRIVATE_KEY is not configured for this process")
    if not token.startswith(WORKER_PREFIX):
        raise DecryptionError("unknown secret format")
    try:
        blob = base64.urlsafe_b64decode(token[len(WORKER_PREFIX) :])
        priv = X25519PrivateKey.from_private_bytes(_raw32(priv_b64, "INTEGRATION_SECRETS_PRIVATE_KEY"))
    except (binascii.Error, ValueError):
        raise DecryptionError("corrupt secret or key") from None
    eph_pub, nonce, ct = blob[:32], blob[32:44], blob[44:]
    try:
        key = _kdf(priv.exchange(X25519PublicKey.from_public_bytes(eph_pub)), eph_pub, purpose)
        return AESGCM(key).decrypt(nonce, ct, purpose.encode())
    except (InvalidTag, ValueError):
        raise DecryptionError("secret cannot be decrypted with the configured private key") from None


def keygen() -> dict[str, str]:
    priv = X25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    return {
        "SECRETS_ENCRYPTION_KEY": base64.b64encode(os.urandom(32)).decode(),
        "INTEGRATION_SECRETS_PUBLIC_KEY": base64.b64encode(_pub_bytes(priv.public_key())).decode(),
        "INTEGRATION_SECRETS_PRIVATE_KEY": base64.b64encode(priv_raw).decode(),
    }


if __name__ == "__main__":  # pragma: no cover - operator helper
    import sys

    if sys.argv[1:] != ["keygen"]:
        sys.exit("usage: python -m app.core.crypto keygen   (prints KEY=value lines for your .env)")
    for name, value in keygen().items():
        print(f"{name}={value}")
