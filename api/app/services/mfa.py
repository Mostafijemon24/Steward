"""Two-step sign-in (TOTP authenticator apps + single-use recovery codes).

- The TOTP seed is encrypted at rest (AES-GCM, bound to the user id) and never returned again
  after setup.
- Turning MFA on/off and regenerating recovery codes need the current password (re-authentication);
  turning it off or regenerating codes also needs a current code.
- Sign-in with MFA: a correct password yields only a 5-minute, single-use challenge (an AuthToken,
  stored hashed, sent as an httpOnly cookie). A session is created only after a valid code.
- A TOTP code can be used once (last_used_step, checked under a row lock); recovery codes are
  single use. Attempts are rate limited per user and per IP.
- A password reset never disables MFA.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core import crypto, totp
from app.core import ratelimit as RL
from app.core.db import utcnow
from app.core.errors import ApiError
from app.core.security import token_hash, verify_password
from app.domain.enums import AuthTokenPurpose
from app.models import AuthSession, AuthToken, MfaRecoveryCode, User, UserMfa, Workspace, WorkspaceMembership
from app.ports import EmailMessage
from app.services.auth import AuthService

CHALLENGE_TTL = timedelta(minutes=5)
RECOVERY_CODE_COUNT = 10
MFA_USER = RL.Limit("mfa_user", 10, 900)
MFA_IP = RL.Limit("mfa_ip", 30, 900)
REAUTH_USER = RL.Limit("reauth_user", 10, 900)
FAILURE_ALERT_AT = 5  # failed second steps within 15 minutes before the owner is e-mailed


def _purpose(user_id: uuid.UUID) -> str:
    return f"mfa-totp:{user_id}"


def _recovery_hash(code: str) -> bytes:
    return hashlib.sha256(("steward-recovery:" + _normalise_recovery_ascii(code)).encode()).digest()


def _new_recovery_code() -> str:
    raw = base64.b32encode(secrets.token_bytes(10)).decode().lower()[:12]  # 60 bits
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}"


def _looks_like_totp(code: str) -> bool:
    digits = "".join(ch for ch in code if not ch.isspace())
    return len(digits) == totp.DIGITS and all(ch in "0123456789" for ch in digits)


def _normalise_recovery_ascii(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch in "abcdefghijklmnopqrstuvwxyz234567")


def cancel_pending_challenges(db: Session, user_id: uuid.UUID) -> None:
    """Pending second-step challenges die with a password reset or when MFA is turned off."""
    db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user_id,
            AuthToken.purpose == AuthTokenPurpose.mfa_login.value,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=utcnow())
    )


class MfaService(AuthService):
    # ------------------------------------------------------------ state
    def _row(self, user: User, lock: bool = False) -> UserMfa | None:
        q = select(UserMfa).where(UserMfa.user_id == user.id)
        if lock:
            q = q.with_for_update()
        return self.db.execute(q).scalar_one_or_none()

    def enabled(self, user: User) -> bool:
        row = self._row(user)
        return bool(row and row.enabled_at and row.secret_enc)

    def status(self, user: User) -> dict[str, object]:
        row = self._row(user)
        remaining = self.db.execute(
            select(func.count())
            .select_from(MfaRecoveryCode)
            .where(
                MfaRecoveryCode.user_id == user.id,
                MfaRecoveryCode.used_at.is_(None),
                MfaRecoveryCode.revoked_at.is_(None),
            )
        ).scalar_one()
        on = bool(row and row.enabled_at and row.secret_enc)
        return {
            "enabled": on,
            "pending_setup": bool(row and row.secret_enc and not row.enabled_at),
            "enabled_at": row.enabled_at if on and row else None,
            "recovery_codes_remaining": int(remaining) if on else 0,
        }

    def _reauth(self, user: User, ws_id: uuid.UUID | None, password: str) -> None:
        RL.hit(REAUTH_USER, str(user.id))
        if not verify_password(user.password_hash, password):
            self._log("auth.reauth_failed", "Password check failed for a security setting", user, ws_id)
            self.db.commit()
            raise ApiError(401, "invalid_password", "That password is not correct.")

    def _secret(self, row: UserMfa) -> str:
        assert row.secret_enc is not None
        return crypto.unseal(row.secret_enc, _purpose(row.user_id)).decode()

    # ------------------------------------------------------------ setup / enable / disable
    def setup(self, user: User, ws_id: uuid.UUID | None, password: str) -> dict[str, str]:
        self._reauth(user, ws_id, password)
        row = self._row(user, lock=True)
        if row and row.enabled_at:
            raise ApiError(
                409, "mfa_already_enabled", "Two-step sign-in is already on. Turn it off first to change it."
            )
        secret = totp.new_secret()
        sealed = crypto.seal(secret.encode(), _purpose(user.id))
        if row is None:
            self.db.add(UserMfa(user_id=user.id, secret_enc=sealed))
        else:
            row.secret_enc = sealed
            row.last_used_step = None
            row.updated_at = utcnow()
        self.db.flush()
        uri = totp.provisioning_uri(secret, user.email)
        import segno

        qr = segno.make(uri, error="m").svg_data_uri(scale=5, border=2)
        self._log("auth.mfa_setup_started", "Two-step sign-in setup started", user, ws_id)
        return {"secret": secret, "otpauth_uri": uri, "qr_svg_data_uri": qr}

    def _new_codes(self, user: User) -> list[str]:
        self.db.execute(
            update(MfaRecoveryCode)
            .where(MfaRecoveryCode.user_id == user.id, MfaRecoveryCode.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        codes = [_new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
        for c in codes:
            self.db.add(MfaRecoveryCode(user_id=user.id, code_hash=_recovery_hash(c)))
        self.db.flush()
        return codes

    def enable(self, user: User, ws_id: uuid.UUID | None, code: str, current_session: AuthSession) -> list[str]:
        RL.hit(MFA_USER, str(user.id))
        row = self._row(user, lock=True)
        if row is None or row.secret_enc is None:
            raise ApiError(409, "mfa_not_set_up", "Start the setup first to get a QR code.")
        if row.enabled_at:
            raise ApiError(409, "mfa_already_enabled", "Two-step sign-in is already on.")
        step = totp.verify(self._secret(row), code, row.last_used_step)
        if step is None:
            self._log("auth.mfa_failed", "Wrong code while turning on two-step sign-in", user, ws_id)
            self.db.commit()
            raise ApiError(400, "invalid_code", "That code is not correct. Check your authenticator app and try again.")
        row.enabled_at = utcnow()
        row.last_used_step = step
        row.updated_at = utcnow()
        codes = self._new_codes(user)
        # Other browsers signed in before MFA existed are signed out; this one stays.
        self.db.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None), AuthSession.id != current_session.id
            )
            .values(revoked_at=utcnow(), revoked_reason="mfa_enabled")
        )
        self._send(
            user.email,
            "Two-step sign-in is now on for your Steward account",
            "Two-step sign-in was turned on. You'll need your authenticator app (or a recovery code) to sign in.\n"
            "Other sessions were signed out. If this wasn't you, reset your password immediately.",
        )
        self._log("auth.mfa_enabled", "Two-step sign-in turned on", user, ws_id)
        return codes

    def check_second_factor(self, user: User, code: str) -> str | None:
        """Return "totp" or "recovery_code" when the code is valid (and consume it), else None."""
        row = self._row(user, lock=True)
        if row is None or not row.enabled_at or row.secret_enc is None:
            return None
        if _looks_like_totp(code):
            step = totp.verify(self._secret(row), code, row.last_used_step)
            if step is None:
                return None
            row.last_used_step = step
            row.updated_at = utcnow()
            return "totp"
        rc = self.db.execute(
            select(MfaRecoveryCode)
            .where(
                MfaRecoveryCode.user_id == user.id,
                MfaRecoveryCode.code_hash == _recovery_hash(code),
                MfaRecoveryCode.used_at.is_(None),
                MfaRecoveryCode.revoked_at.is_(None),
            )
            .with_for_update()
        ).scalar_one_or_none()
        if rc is None:
            return None
        rc.used_at = utcnow()
        return "recovery_code"

    def disable(self, user: User, ws_id: uuid.UUID | None, password: str, code: str) -> None:
        self._reauth(user, ws_id, password)
        RL.hit(MFA_USER, str(user.id))
        if not self.enabled(user):
            raise ApiError(409, "mfa_not_enabled", "Two-step sign-in is not on.")
        if self.check_second_factor(user, code) is None:
            self._log("auth.mfa_failed", "Wrong code while turning off two-step sign-in", user, ws_id)
            self.db.commit()
            raise ApiError(400, "invalid_code", "That code is not correct.")
        row = self._row(user, lock=True)
        assert row is not None
        row.enabled_at = None
        row.secret_enc = None
        row.last_used_step = None
        row.updated_at = utcnow()
        cancel_pending_challenges(self.db, user.id)
        self.db.execute(
            update(MfaRecoveryCode)
            .where(MfaRecoveryCode.user_id == user.id, MfaRecoveryCode.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        self._send(
            user.email,
            "Two-step sign-in was turned off for your Steward account",
            "Two-step sign-in was turned off. If this wasn't you, reset your password and turn it on again.",
        )
        self._log("auth.mfa_disabled", "Two-step sign-in turned off", user, ws_id)

    def regenerate_codes(self, user: User, ws_id: uuid.UUID | None, password: str, code: str) -> list[str]:
        self._reauth(user, ws_id, password)
        RL.hit(MFA_USER, str(user.id))
        if not self.enabled(user):
            raise ApiError(409, "mfa_not_enabled", "Two-step sign-in is not on.")
        if self.check_second_factor(user, code) is None:
            self._log("auth.mfa_failed", "Wrong code while creating new recovery codes", user, ws_id)
            self.db.commit()
            raise ApiError(400, "invalid_code", "That code is not correct.")
        codes = self._new_codes(user)
        self._log("auth.mfa_recovery_regenerated", "New recovery codes created (old ones stopped working)", user, ws_id)
        return codes

    def _warn_on_repeated_failures(self, user: User) -> None:
        """Tell the owner once when the second step keeps failing: their password is probably known."""
        from datetime import timedelta as _td

        from app.models import AuditEvent
        from app.services.auth import deliver_mail

        recent = self.db.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.actor_user_id == user.id,
                AuditEvent.action == "auth.mfa_failed",
                AuditEvent.created_at > utcnow() - _td(minutes=15),
            )
        ).scalar_one()
        if recent != FAILURE_ALERT_AT:
            return
        import threading

        msg = EmailMessage(
            to=user.email,
            subject="Repeated failed sign-in attempts on your Steward account",
            text=(
                "Someone entered your correct password but failed the two-step sign-in code several times.\n"
                "Your account is still protected, but your password is probably known to someone else.\n"
                "Change your password now (use 'Forgot password' on the sign-in page)."
            ),
        )
        threading.Thread(target=deliver_mail, args=(self.settings, [msg]), daemon=True).start()

    # ------------------------------------------------------------ sign-in second step
    def start_challenge(self, user: User, ws_id: uuid.UUID | None) -> str:
        raw = self._issue_token(user, AuthTokenPurpose.mfa_login, CHALLENGE_TTL)
        self._log("auth.mfa_challenge", "Password accepted; waiting for the second step", user, ws_id)
        return raw

    def complete_challenge(
        self, raw: str, code: str, user_agent: str | None
    ) -> tuple[str, User, Workspace, WorkspaceMembership]:
        RL.hit(MFA_IP, self.ip or "unknown")
        tok = self.db.execute(
            select(AuthToken)
            .where(AuthToken.token_hash == token_hash(raw), AuthToken.purpose == AuthTokenPurpose.mfa_login.value)
            .with_for_update()
        ).scalar_one_or_none()
        if tok is None or tok.used_at is not None or tok.expires_at <= utcnow():
            raise ApiError(
                401, "mfa_challenge_expired", "Your sign-in step expired. Enter your email and password again."
            )
        user = self.db.get(User, tok.user_id)
        assert user is not None
        RL.hit(MFA_USER, str(user.id))
        ws = self._primary_workspace(user.id)
        method = self.check_second_factor(user, code) if user.email_verified_at and ws else None
        if method is None or ws is None:
            self._log("auth.mfa_failed", "Wrong second-step code at sign-in", user, ws.id if ws else None)
            self.db.commit()
            self._warn_on_repeated_failures(user)
            raise ApiError(401, "invalid_code", "That code is not correct. Check your authenticator app and try again.")
        tok.used_at = utcnow()
        membership = self.db.execute(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == ws.id, WorkspaceMembership.user_id == user.id
            )
        ).scalar_one()
        session_raw = self.create_session(user, ws, user_agent)
        self._log("auth.login", "Signed in with two-step sign-in", user, ws.id, method=method)
        if method == "recovery_code":
            self._log("auth.mfa_recovery_used", "A recovery code was used to sign in", user, ws.id)
            self._send(
                user.email,
                "A recovery code was used to sign in to Steward",
                "Someone signed in with one of your recovery codes. If this was you, consider creating new codes.\n"
                "If it wasn't you, reset your password and create new recovery codes immediately.",
            )
        return session_raw, user, ws, membership
