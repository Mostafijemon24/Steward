"""MFA (TOTP + recovery codes) and encrypted integration credentials (Phase 5).

- user_mfa: encrypted TOTP seed per user; mfa_recovery_codes: hashed single-use codes.
- integration_secrets: encrypted credentials (WordPress Application Password).
- auth_tokens.purpose gains 'mfa_login' (the short-lived second-step challenge).

Privileges (least privilege, no DELETE anywhere):
- steward_app: MFA tables SELECT/INSERT/UPDATE; integration_secrets INSERT/UPDATE and SELECT on
  every column EXCEPT secret_enc — the API stores a credential but can never read it back.
- steward_worker: SELECT on integration_secrets (the tools worker decrypts right before a call).
- steward_deleter / steward_readonly: no access to any of these tables.

Additive: no data changes.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-24
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP = os.environ.get("DB_APP_ROLE", "steward_app").replace('"', "")
WORKER = os.environ.get("DB_WORKER_ROLE", "steward_worker").replace('"', "")
SECRET_META_COLUMNS = "id, workspace_id, key, created_by, created_at, updated_at, revoked_at"


def upgrade() -> None:
    op.create_table(
        "integration_secrets",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("secret_enc", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_integration_secrets_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_integration_secrets_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_secrets")),
        sa.UniqueConstraint("workspace_id", "key", name=op.f("uq_integration_secrets_workspace_id_key")),
    )
    op.create_table(
        "mfa_recovery_codes",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("code_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_mfa_recovery_codes_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mfa_recovery_codes")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_mfa_recovery_codes_code_hash")),
    )
    op.create_index("ix_mfa_recovery_codes_user", "mfa_recovery_codes", ["user_id"], unique=False)
    op.create_table(
        "user_mfa",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("secret_enc", sa.Text(), nullable=True),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_step", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_user_mfa_user_id_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_mfa")),
    )
    op.drop_constraint(op.f("ck_auth_tokens_purpose_valid"), "auth_tokens", type_="check")
    op.create_check_constraint(
        op.f("ck_auth_tokens_purpose_valid"),
        "auth_tokens",
        "purpose IN ('verify_email', 'reset_password', 'mfa_login')",
    )

    op.execute(f'GRANT SELECT, INSERT, UPDATE ON user_mfa, mfa_recovery_codes TO "{APP}"')
    op.execute(f'GRANT INSERT, UPDATE ON integration_secrets TO "{APP}"')
    op.execute(f'GRANT SELECT ({SECRET_META_COLUMNS}) ON integration_secrets TO "{APP}"')
    op.execute(f'GRANT SELECT ON integration_secrets TO "{WORKER}"')


def downgrade() -> None:
    op.execute("DELETE FROM auth_tokens WHERE purpose = 'mfa_login'")
    op.drop_constraint(op.f("ck_auth_tokens_purpose_valid"), "auth_tokens", type_="check")
    op.create_check_constraint(
        op.f("ck_auth_tokens_purpose_valid"), "auth_tokens", "purpose IN ('verify_email', 'reset_password')"
    )

    op.drop_table("user_mfa")
    op.drop_index("ix_mfa_recovery_codes_user", table_name="mfa_recovery_codes")
    op.drop_table("mfa_recovery_codes")
    op.drop_table("integration_secrets")
