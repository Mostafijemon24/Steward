"""Two-step sign-in (TOTP + recovery codes) and encryption at rest — including the failure cases."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from conftest import PASSWORD, Api, latest_token, outbox_messages
from pydantic import SecretStr
from sqlalchemy import text

from app.core import crypto, totp
from app.core.config import get_settings

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


# ---------------------------------------------------------------- unit: TOTP and crypto
def test_totp_matches_rfc_6238_vectors() -> None:
    # RFC 6238 Appendix B, SHA-1 (last 6 of the 8-digit values).
    for t, expected in [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")]:
        assert totp.code_at(RFC_SECRET, t // 30) == expected


def test_totp_window_and_replay() -> None:
    now = 1_700_000_000.0
    step = totp.current_step(now)
    code = totp.code_at(RFC_SECRET, step)
    assert totp.verify(RFC_SECRET, code, None, now) == step
    assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, step - 1), None, now) == step - 1  # 30 s drift ok
    assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, step - 2), None, now) is None  # too old
    assert totp.verify(RFC_SECRET, code, step, now) is None  # replay of the same step
    assert totp.verify(RFC_SECRET, "12345", None, now) is None and totp.verify(RFC_SECRET, "abcdef", None, now) is None


def test_crypto_binds_purpose_and_detects_tampering(monkeypatch: Any) -> None:
    sealed = crypto.seal(b"top secret", "mfa-totp:user-a")
    assert sealed.startswith("v1:") and "top secret" not in sealed
    assert crypto.unseal(sealed, "mfa-totp:user-a") == b"top secret"
    with pytest.raises(crypto.DecryptionError):
        crypto.unseal(sealed, "mfa-totp:user-b")  # copied to another user's row
    raw = bytearray(base64.urlsafe_b64decode(sealed[3:]))
    raw[-1] ^= 1
    with pytest.raises(crypto.DecryptionError):
        crypto.unseal("v1:" + base64.urlsafe_b64encode(bytes(raw)).decode(), "mfa-totp:user-a")
    # Key rotation: old values still open with the previous key; new values use the new key.
    s = get_settings()
    old_key = s.secrets_encryption_key
    new_key = SecretStr(base64.b64encode(b"n" * 32).decode())
    monkeypatch.setattr(s, "secrets_encryption_key", new_key)
    with pytest.raises(crypto.DecryptionError):
        crypto.unseal(sealed, "mfa-totp:user-a")
    monkeypatch.setattr(s, "secrets_encryption_previous_key", old_key)
    assert crypto.unseal(sealed, "mfa-totp:user-a") == b"top secret"


def test_production_refuses_the_development_encryption_key(monkeypatch: Any) -> None:
    from pydantic import ValidationError

    from app.core.config import DEV_SECRETS_KEY, Settings

    base: dict[str, Any] = {
        "env": "production",
        "app_origin": "https://app.example.com",
        "mail_backend": "smtp",
        "smtp_host": "smtp.example.com",
        "trust_proxy_headers": True,
        "database_url": "postgresql+psycopg://a:Str0ng-Random-Value@db/x",
        "approval_signing_key": "k" * 40,
        "secrets_encryption_key": base64.b64encode(b"r" * 32).decode(),
        "integration_secrets_public_key": base64.b64encode(b"p" * 32).decode(),
    }
    Settings(**base)
    with pytest.raises(ValidationError, match="SECRETS_ENCRYPTION_KEY"):
        Settings(**{**base, "secrets_encryption_key": DEV_SECRETS_KEY})
    with pytest.raises(ValidationError, match="SECRETS_ENCRYPTION_KEY"):
        Settings(**{**base, "secrets_encryption_key": base64.b64encode(b"short").decode()})
    # A worker without the key may start (it never decrypts) ...
    worker = {**base, "process_role": "worker", "mail_backend": "disabled", "secrets_encryption_key": DEV_SECRETS_KEY}
    Settings(**worker)
    # ... but decryption then refuses the public development key in production.
    monkeypatch.setattr(get_settings(), "env", "production")
    with pytest.raises(crypto.DecryptionError):
        crypto.seal(b"x", "p")


# ---------------------------------------------------------------- flow helpers
def _code(secret: str, offset: int = 0) -> str:
    return totp.code_at(secret, totp.current_step() + offset)


def _enable(api: Api) -> tuple[str, list[str]]:
    r = api.post("/me/mfa/setup", {"password": PASSWORD})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert r.json()["otpauth_uri"].startswith("otpauth://totp/Steward") and r.json()["qr_svg_data_uri"].startswith(
        "data:image/svg+xml"
    )
    r = api.post("/me/mfa/enable", {"code": _code(secret)})
    assert r.status_code == 200, r.text
    codes = r.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    return secret, codes


def _password_step(app: Any, email: str) -> Api:
    c = Api(app)
    r = c.post("/auth/login", {"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json() == {"mfa_required": True, "methods": ["totp", "recovery_code"]}
    return c


# ---------------------------------------------------------------- flows
def test_enable_requires_password_and_correct_code(make_user: Any) -> None:
    api = make_user()
    assert api.post("/me/mfa/setup", {"password": "wrong-password-1"}).json()["error"]["code"] == "invalid_password"
    assert api.post("/me/mfa/enable", {"code": "123456"}).json()["error"]["code"] == "mfa_not_set_up"
    secret = api.post("/me/mfa/setup", {"password": PASSWORD}).json()["secret"]
    bad = str((int(_code(secret)) + 1) % 1_000_000).zfill(6)
    r = api.post("/me/mfa/enable", {"code": bad})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_code"
    assert api.get("/me/mfa").json()["enabled"] is False and api.get("/me/mfa").json()["pending_setup"] is True


def test_sign_in_needs_the_second_step(app: Any, make_user: Any, owner_engine: Any) -> None:
    api = make_user()
    other = Api(app)  # a second browser signed in before MFA was turned on
    assert other.post("/auth/login", {"email": api.email, "password": PASSWORD}).status_code == 200
    secret, _ = _enable(api)
    assert api.get("/me").status_code == 200  # this browser stays signed in
    assert other.get("/me").status_code == 401  # other sessions were signed out
    st = api.get("/me/mfa").json()
    assert st["enabled"] is True and st["recovery_codes_remaining"] == 10 and "secret" not in st

    # The seed is stored encrypted, never in clear text.
    with owner_engine.connect() as c:
        stored = c.execute(
            text("SELECT secret_enc FROM user_mfa m JOIN users u ON u.id = m.user_id WHERE u.email = :e"),
            {"e": api.email},
        ).scalar_one()
    assert stored.startswith("v1:") and secret not in stored

    c = _password_step(app, api.email)
    assert c.get("/me").status_code == 401  # a password alone gives no session
    bad = str((int(_code(secret)) + 1) % 1_000_000).zfill(6)
    r = c.post("/auth/mfa/verify", {"code": bad})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_code"
    # A wrong code does not burn the challenge; the right one completes sign-in.
    r = c.post("/auth/mfa/verify", {"code": _code(secret, 1)})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == api.email and c.get("/me").status_code == 200
    # The challenge is single use.
    assert c.post("/auth/mfa/verify", {"code": _code(secret, 1)}).json()["error"]["code"] == "mfa_challenge_expired"

    # Replay: the same code (same time step) is refused on the next sign-in.
    c2 = _password_step(app, api.email)
    assert c2.post("/auth/mfa/verify", {"code": _code(secret, 1)}).json()["error"]["code"] == "invalid_code"


def test_verify_without_or_with_expired_challenge(app: Any, make_user: Any, owner_engine: Any) -> None:
    api = make_user()
    secret, _ = _enable(api)
    anon = Api(app)
    r = anon.post("/auth/mfa/verify", {"code": _code(secret)})
    assert r.status_code == 401 and r.json()["error"]["code"] == "mfa_challenge_expired"
    c = _password_step(app, api.email)
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE auth_tokens SET expires_at = now() - interval '1 second' WHERE purpose = 'mfa_login'")
        )
    r = c.post("/auth/mfa/verify", {"code": _code(secret)})
    assert r.status_code == 401 and r.json()["error"]["code"] == "mfa_challenge_expired"
    assert c.get("/me").status_code == 401


def test_recovery_codes_are_single_use_and_announced(app: Any, make_user: Any) -> None:
    api = make_user()
    _secret, codes = _enable(api)
    c = _password_step(app, api.email)
    assert c.post("/auth/mfa/verify", {"code": codes[0].upper().replace("-", " ")}).status_code == 200
    assert any("recovery code was used" in m["subject"] for m in outbox_messages(api.email))
    c2 = _password_step(app, api.email)
    assert c2.post("/auth/mfa/verify", {"code": codes[0]}).json()["error"]["code"] == "invalid_code"
    assert api.get("/me/mfa").json()["recovery_codes_remaining"] == 9


def test_second_step_is_rate_limited(app: Any, make_user: Any) -> None:
    api = make_user()
    secret, _ = _enable(api)
    c = _password_step(app, api.email)
    bad = str((int(_code(secret)) + 7) % 1_000_000).zfill(6)
    statuses = [c.post("/auth/mfa/verify", {"code": bad}).status_code for _ in range(12)]
    assert 429 in statuses and statuses.index(429) <= 10
    # Even the right code is refused while limited.
    assert c.post("/auth/mfa/verify", {"code": _code(secret)}).status_code == 429


def test_disable_and_regenerate_need_password_and_code(app: Any, make_user: Any) -> None:
    api = make_user()
    secret, codes = _enable(api)
    assert api.post("/me/mfa/disable", {"password": "wrong-password-1", "code": _code(secret)}).status_code == 401
    assert api.post("/me/mfa/disable", {"password": PASSWORD, "code": "000000"}).status_code in (400,)
    r = api.post("/me/mfa/recovery-codes", {"password": PASSWORD, "code": codes[1]})
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10
    new_codes = r.json()["recovery_codes"]
    # Old codes stopped working.
    c = _password_step(app, api.email)
    assert c.post("/auth/mfa/verify", {"code": codes[2]}).json()["error"]["code"] == "invalid_code"
    r = api.post("/me/mfa/disable", {"password": PASSWORD, "code": new_codes[0]})
    assert r.status_code == 200 and r.json()["enabled"] is False
    plain = Api(app)
    r = plain.post("/auth/login", {"email": api.email, "password": PASSWORD})
    assert r.status_code == 200 and "user" in r.json()  # back to password-only


def test_password_reset_does_not_bypass_mfa(app: Any, make_user: Any) -> None:
    api = make_user()
    secret, _ = _enable(api)
    anon = Api(app)
    anon.post("/auth/forgot-password", {"email": api.email})
    token = latest_token(api.email, "/reset-password")
    new_pw = "brand-new-pass-99"
    assert anon.post("/auth/reset-password", {"token": token, "password": new_pw}).status_code == 200
    c = Api(app)
    r = c.post("/auth/login", {"email": api.email, "password": new_pw})
    assert r.status_code == 200 and r.json().get("mfa_required") is True
    assert c.get("/me").status_code == 401
    assert c.post("/auth/mfa/verify", {"code": _code(secret, 1)}).status_code == 200


def test_wrong_password_never_reveals_mfa_state(app: Any, make_user: Any) -> None:
    api = make_user()
    _enable(api)
    c = Api(app)
    r = c.post("/auth/login", {"email": api.email, "password": "wrong-password-1"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"
    assert "mfa" not in r.text.lower()


# ---------------------------------------------------------------- review findings (P5-R3, R4, R9)
@pytest.mark.parametrize(
    "code", ["١٢٣٤٥٦", "12345²", "\uff11\uff12\uff13\uff14\uff15\uff16"]
)  # Arabic-Indic, superscript, full-width
def test_non_ascii_digits_are_rejected_cleanly(app: Any, make_user: Any, code: str) -> None:
    api = make_user()
    secret, _ = _enable(api)
    assert totp.verify(secret, code, None) is None
    c = _password_step(app, api.email)
    r = c.post("/auth/mfa/verify", {"code": code})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_code"
    r = api.post("/me/mfa/disable", {"password": PASSWORD, "code": code})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_code"


def test_password_reset_cancels_pending_challenges(app: Any, make_user: Any) -> None:
    api = make_user()
    secret, _ = _enable(api)
    attacker = _password_step(app, api.email)  # has the old password, waiting for a code
    anon = Api(app)
    anon.post("/auth/forgot-password", {"email": api.email})
    token = latest_token(api.email, "/reset-password")
    assert anon.post("/auth/reset-password", {"token": token, "password": "brand-new-pass-77"}).status_code == 200
    r = attacker.post("/auth/mfa/verify", {"code": _code(secret, 1)})
    assert r.status_code == 401 and r.json()["error"]["code"] == "mfa_challenge_expired"


def test_turning_mfa_off_cancels_pending_challenges(app: Any, make_user: Any) -> None:
    api = make_user()
    secret, codes = _enable(api)
    pending = _password_step(app, api.email)
    assert api.post("/me/mfa/disable", {"password": PASSWORD, "code": codes[0]}).status_code == 200
    r = pending.post("/auth/mfa/verify", {"code": _code(secret, 1)})
    assert r.json()["error"]["code"] == "mfa_challenge_expired"


def test_owner_is_warned_once_about_repeated_second_step_failures(app: Any, make_user: Any) -> None:
    import time

    api = make_user()
    secret, _ = _enable(api)
    c = _password_step(app, api.email)
    bad = str((int(_code(secret)) + 3) % 1_000_000).zfill(6)
    for _ in range(7):
        c.post("/auth/mfa/verify", {"code": bad})
    deadline = time.time() + 5
    warned: list[dict[str, str]] = []
    while time.time() < deadline and not warned:
        warned = [m for m in outbox_messages(api.email) if "Repeated failed sign-in" in m["subject"]]
        time.sleep(0.1)
    time.sleep(0.5)
    warned = [m for m in outbox_messages(api.email) if "Repeated failed sign-in" in m["subject"]]
    assert len(warned) == 1 and "password is probably known" in warned[0]["body"]
