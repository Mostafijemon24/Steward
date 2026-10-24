"""Authentication: registration, login/logout, sessions, email verification, password reset.

Anti-enumeration: register, login, resend-verification and forgot-password
return the same response whether or not an account exists. Login requires a
verified email, and every login failure uses one generic message.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.adapters.mail import build_mailer
from app.core.config import Settings, get_settings
from app.core.db import utcnow
from app.core.errors import ApiError
from app.core.security import hash_password, needs_rehash, new_token, token_hash, verify_password
from app.domain.enums import AuthTokenPurpose, WorkspaceRole
from app.models import AuthSession, AuthToken, Notification, User, Workspace, WorkspaceMembership
from app.ports import AuditRecord, EmailMessage
from app.services.audit import DbAuditLogger

log = logging.getLogger("steward.auth")

GENERIC_LOGIN_ERROR = (
    "That email and password combination did not match, or the email address hasn't been verified yet."
)
REGISTER_MESSAGE = (
    "Check your inbox. If this address can be used, we sent a link to verify it. Open the link, then sign in."
)
FORGOT_MESSAGE = (
    "If an account exists for that email, we sent a link to reset the password. The link expires in 1 hour."
)
RESEND_MESSAGE = "If that address has an account waiting for verification, we sent a new link."

VERIFY_TTL = timedelta(hours=24)
RESET_TTL = timedelta(hours=1)

DEFAULT_AI_RULES = {"share": True, "publish": True, "prod": True, "del": True, "folders": True}
DEFAULT_TOOLS = {"wp_read": True, "wp_write": False, "browser": True, "email": False, "external": False}
DEFAULT_NOTIFICATIONS = {"approval": True, "done": True, "failed": True, "security": True, "digest": False}


def deliver_mail(settings: Settings, messages: list[EmailMessage]) -> None:
    """Runs after the HTTP response. Failures are logged (masked address), never surfaced to the client."""
    mailer = build_mailer(settings)
    for m in messages:
        try:
            mailer.send(m)
        except Exception:
            local, _, domain = m.to.partition("@")
            log.warning("mail delivery failed to=%s***@%s subject=%r", local[:1], domain, m.subject)


class AuthService:
    def __init__(self, db: Session, ip: str | None, request_id: str, settings: Settings | None = None) -> None:
        self.db = db
        self.ip = ip
        self.request_id = request_id
        self.settings = settings or get_settings()
        self.audit = DbAuditLogger(db)
        self.pending_mail: list[EmailMessage] = []

    # ------------------------------------------------------------ helpers
    def _log(
        self, action: str, message: str, user: User | None = None, ws_id: uuid.UUID | None = None, **meta: object
    ) -> None:
        self.audit.record(
            AuditRecord(
                action=action,
                category="auth",
                message=message,
                workspace_id=ws_id,
                actor_user_id=user.id if user else None,
                ip=self.ip,
                request_id=self.request_id,
                metadata=dict(meta),
            )
        )

    def _primary_workspace(self, user_id: uuid.UUID) -> Workspace | None:
        return self.db.execute(
            select(Workspace)
            .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
            .where(WorkspaceMembership.user_id == user_id)
            .order_by(WorkspaceMembership.created_at)
            .limit(1)
        ).scalar_one_or_none()

    def _issue_token(self, user: User, purpose: AuthTokenPurpose, ttl: timedelta) -> str:
        # Invalidate older unused tokens of the same purpose.
        self.db.execute(
            update(AuthToken)
            .where(AuthToken.user_id == user.id, AuthToken.purpose == purpose.value, AuthToken.used_at.is_(None))
            .values(used_at=utcnow())
        )
        raw = new_token()
        self.db.add(
            AuthToken(user_id=user.id, purpose=purpose.value, token_hash=token_hash(raw), expires_at=utcnow() + ttl)
        )
        self.db.flush()
        return raw

    def _consume_token(self, raw: str, purpose: AuthTokenPurpose) -> User:
        row = self.db.execute(
            select(AuthToken)
            .where(AuthToken.token_hash == token_hash(raw), AuthToken.purpose == purpose.value)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None or row.used_at is not None or row.expires_at <= utcnow():
            raise ApiError(400, "invalid_or_expired_token", "This link is invalid or has expired. Request a new one.")
        row.used_at = utcnow()
        user = self.db.get(User, row.user_id)
        assert user is not None
        return user

    def _send(self, to: str, subject: str, text: str) -> None:
        # Queued, not sent: the router delivers after the response (see deliver_mail), so
        # mail latency or failures can never reveal whether an account exists.
        self.pending_mail.append(EmailMessage(to=to, subject=subject, text=text))

    def _link(self, path: str, token: str) -> str:
        return f"{self.settings.app_origin.rstrip('/')}{path}?token={token}"

    # ------------------------------------------------------------ register
    def register(self, name: str, email: str, password: str) -> str:
        email = email.lower()
        existing = self.db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if existing is not None:
            # Same response and the same expensive work as a new registration (no timing oracle);
            # tell the real owner instead.
            hash_password(password)
            self._send(
                email,
                "Someone tried to create a Steward account with your email",
                "Someone (maybe you) tried to register a new Steward account with this email address.\n\n"
                "You already have an account. If you forgot your password, use 'Forgot password' on the sign-in page.\n"
                "If this wasn't you, you can ignore this email.",
            )
            ws = self._primary_workspace(existing.id)
            self._log(
                "auth.register_existing_email",
                "Registration attempted for an existing account",
                existing,
                ws.id if ws else None,
            )
            return REGISTER_MESSAGE

        user = User(
            name=name, email=email, password_hash=hash_password(password), notification_prefs=DEFAULT_NOTIFICATIONS
        )
        self.db.add(user)
        self.db.flush()
        first = name.split()[0] if name.split() else name
        ws = Workspace(name=f"{first}'s workspace"[:120], ai_rules=DEFAULT_AI_RULES, tool_permissions=DEFAULT_TOOLS)
        self.db.add(ws)
        self.db.flush()
        self.db.add(WorkspaceMembership(workspace_id=ws.id, user_id=user.id, role=WorkspaceRole.owner.value))
        self.db.add(
            Notification(
                workspace_id=ws.id,
                user_id=user.id,
                type="Workspace ready",
                icon="circle-check",
                text="Your private workspace is ready. The AI agent engine is not installed yet, so tasks are saved but not executed.",
                target={"route": "/help"},
            )
        )
        raw = self._issue_token(user, AuthTokenPurpose.verify_email, VERIFY_TTL)
        self._send(
            email,
            "Verify your email for Steward",
            f"Hello,\n\nOpen this link to verify your email address (valid for 24 hours):\n{self._link('/verify-email', raw)}\n\n"
            "If you didn't create an account, you can ignore this email.",
        )
        self._log("auth.registered", "Account created — waiting for email verification", user, ws.id)
        return REGISTER_MESSAGE

    def verify_email(self, raw: str) -> None:
        user = self._consume_token(raw, AuthTokenPurpose.verify_email)
        if user.email_verified_at is None:
            user.email_verified_at = utcnow()
        ws = self._primary_workspace(user.id)
        self._log("auth.email_verified", "Email address verified", user, ws.id if ws else None)

    def resend_verification(self, email: str) -> str:
        user = self.db.execute(select(User).where(User.email == email.lower())).scalar_one_or_none()
        if user is not None and user.email_verified_at is None:
            raw = self._issue_token(user, AuthTokenPurpose.verify_email, VERIFY_TTL)
            self._send(
                user.email,
                "Verify your email for Steward",
                f"Open this link to verify your email address:\n{self._link('/verify-email', raw)}",
            )
            ws = self._primary_workspace(user.id)
            self._log("auth.verification_resent", "Verification email sent again", user, ws.id if ws else None)
        return RESEND_MESSAGE

    # ------------------------------------------------------------ login / logout
    def login(
        self, email: str, password: str, user_agent: str | None
    ) -> tuple[str, User, Workspace, WorkspaceMembership]:
        user, ws, membership = self.authenticate(email, password)
        raw = self.create_session(user, ws, user_agent)
        self._log("auth.login", "Signed in", user, ws.id)
        return raw, user, ws, membership

    def authenticate(self, email: str, password: str) -> tuple[User, Workspace, WorkspaceMembership]:
        """Check email + password only (no session). Failures are audited and generic."""
        email = email.lower()
        user = self.db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        ok = verify_password(user.password_hash if user else None, password)
        ws = self._primary_workspace(user.id) if user else None
        if not ok or user is None or user.email_verified_at is None or ws is None:
            reason = "unknown_account" if user is None else ("unverified" if ok else "bad_password")
            self._log("auth.login_failed", "Sign-in attempt failed", user, ws.id if ws else None, reason=reason)
            self.db.commit()  # keep the audit row even though we raise
            raise ApiError(401, "invalid_credentials", GENERIC_LOGIN_ERROR)
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        membership = self.db.execute(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == ws.id, WorkspaceMembership.user_id == user.id
            )
        ).scalar_one()
        return user, ws, membership

    def create_session(self, user: User, ws: Workspace, user_agent: str | None) -> str:
        now = utcnow()
        raw = new_token()
        self.db.add(
            AuthSession(
                user_id=user.id,
                workspace_id=ws.id,
                token_hash=token_hash(raw),
                last_seen_at=now,
                idle_expires_at=now + timedelta(hours=self.settings.session_idle_hours),
                expires_at=now + timedelta(days=self.settings.session_absolute_days),
                ip=self.ip,
                user_agent=(user_agent or "")[:256] or None,
            )
        )
        self.db.flush()
        return raw

    def revoke_presented_session(self, raw: str) -> None:
        self.db.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == token_hash(raw), AuthSession.revoked_at.is_(None))
            .values(revoked_at=utcnow(), revoked_reason="replaced_by_login")
        )

    def logout(self, sess: AuthSession, user: User, ws_id: uuid.UUID) -> None:
        sess.revoked_at = utcnow()
        sess.revoked_reason = "logout"
        self._log("auth.logout", "Signed out", user, ws_id)

    # ------------------------------------------------------------ password reset
    def forgot_password(self, email: str) -> str:
        user = self.db.execute(select(User).where(User.email == email.lower())).scalar_one_or_none()
        if user is not None:
            raw = self._issue_token(user, AuthTokenPurpose.reset_password, RESET_TTL)
            self._send(
                user.email,
                "Reset your Steward password",
                f"Open this link to choose a new password (valid for 1 hour):\n{self._link('/reset-password', raw)}\n\n"
                "If you didn't ask for this, you can ignore this email — your password stays the same.",
            )
            ws = self._primary_workspace(user.id)
            self._log("auth.password_reset_requested", "Password reset requested", user, ws.id if ws else None)
        return FORGOT_MESSAGE

    def reset_password(self, raw: str, password: str) -> None:
        user = self._consume_token(raw, AuthTokenPurpose.reset_password)
        user.password_hash = hash_password(password)
        user.password_changed_at = utcnow()
        # Proving control of the inbox also verifies the address.
        if user.email_verified_at is None:
            user.email_verified_at = utcnow()
        # Account recovery ends every existing session and every pending second-step challenge.
        self.db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=utcnow(), revoked_reason="password_reset")
        )
        self.db.execute(
            update(AuthToken)
            .where(
                AuthToken.user_id == user.id,
                AuthToken.purpose == AuthTokenPurpose.mfa_login.value,
                AuthToken.used_at.is_(None),
            )
            .values(used_at=utcnow())
        )
        self._send(
            user.email,
            "Your Steward password was changed",
            "Your password was just changed and all sessions were signed out.\n"
            "If this wasn't you, reset your password again immediately.",
        )
        ws = self._primary_workspace(user.id)
        self._log("auth.password_reset", "Password changed — all sessions signed out", user, ws.id if ws else None)
