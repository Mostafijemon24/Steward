"""Integration framework.

Only integrations that genuinely work are marked connected. The first real one is WordPress
read-only via the site's public REST API: no credentials are requested or stored, the site's
domain must be on the workspace allowlist, and the connection is verified from the tools worker
(through the egress client) before it is marked connected.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import utcnow
from app.engine import queue as Q
from app.engine.egress import EgressBlocked, EgressClient, EgressFailed, workspace_policy
from app.engine.records import audit
from app.models import IntegrationConnection, Workspace

# key -> how it works. "operational" means a real adapter exists and is tested.
INTEGRATIONS: dict[str, dict[str, Any]] = {
    "wordpress": {
        "operational": True,
        "mode": "read-only (public REST API), optional draft posts with an encrypted Application Password",
    },
    "notion": {"operational": False, "mode": "not built"},
    "cloud_storage": {"operational": False, "mode": "not built"},
    "email": {"operational": False, "mode": "not built"},
    "browser": {"operational": False, "mode": "not built (use the read-only page inspection tool)"},
    "ai_provider": {"operational": True, "mode": "configured by the server administrator (environment variables)"},
}


def handle_verify(db: Session, job: Q.ClaimedJob, settings: Settings) -> None:
    key = str(job.payload.get("key"))
    conn = db.execute(
        select(IntegrationConnection)
        .where(IntegrationConnection.workspace_id == job.workspace_id, IntegrationConnection.key == key)
        .with_for_update()
    ).scalar_one_or_none()
    if conn is None or key != "wordpress":
        return
    site = str((conn.config or {}).get("site_url") or "")
    ws = db.execute(select(Workspace).where(Workspace.id == job.workspace_id)).scalar_one()
    client = EgressClient(workspace_policy(ws.allowed_domains or [], settings), settings)
    error: str | None = None
    try:
        resp = client.get(site.rstrip("/") + "/wp-json/", headers={"Accept": "application/json"})
        if resp.status >= 400:
            error = f"The site returned HTTP {resp.status} for its REST API."
        else:
            data = json.loads(resp.body)
            if not isinstance(data, dict) or "wp/v2" not in (data.get("namespaces") or []):
                error = "This does not look like a WordPress site with the REST API enabled."
    except EgressBlocked as e:
        error = e.message
    except EgressFailed as e:
        error = e.message
    except ValueError:
        error = "The site's REST API did not return JSON."
    write = (conn.config or {}).get("mode") == "read_write_drafts"
    if not error and write:
        error = _verify_write(db, client, job, site)
    conn.status = "error" if error else "connected"
    conn.error_message = error
    conn.last_activity_at = utcnow()
    conn.updated_at = utcnow()
    audit(
        db,
        job.workspace_id,
        "integration.verified" if not error else "integration.verify_failed",
        "integration",
        f"WordPress {('connected (read + draft posts)' if write else 'connected (read-only)') if not error else 'connection failed: ' + error}",
        target_type="integration",
        target_id=key,
        job_id=job.id,
    )


def _verify_write(db: Session, client: EgressClient, job: Q.ClaimedJob, site: str) -> str | None:
    """Check the stored Application Password works and may create posts. Returns an error or None."""
    from app.core import crypto
    from app.engine.tools.wp_tools import basic_auth, secret_purpose
    from app.models import IntegrationSecret

    sealed = db.execute(
        select(IntegrationSecret.secret_enc).where(
            IntegrationSecret.workspace_id == job.workspace_id,
            IntegrationSecret.key == "wordpress",
            IntegrationSecret.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if not sealed:
        return "No credentials are stored for write access."
    try:
        cred = json.loads(crypto.unseal_on_worker(sealed, secret_purpose(job.workspace_id)))
    except (crypto.DecryptionError, ValueError):
        return (
            "The stored credentials could not be decrypted on the tools worker (check INTEGRATION_SECRETS_PRIVATE_KEY)."
        )
    try:
        resp = client.get(
            site.rstrip("/") + "/wp-json/wp/v2/users/me?context=edit",
            headers={"Accept": "application/json", "Authorization": basic_auth(cred["username"], cred["password"])},
        )
    except (EgressBlocked, EgressFailed) as e:
        return e.message
    if resp.status in (401, 403):
        return "WordPress refused the username or Application Password."
    if resp.status >= 400:
        return f"WordPress returned HTTP {resp.status} when checking the credentials."
    try:
        caps = json.loads(resp.body).get("capabilities") or {}
    except (ValueError, AttributeError):
        return "WordPress did not return the user's details."
    if not caps.get("edit_posts"):
        return "This WordPress user is not allowed to create posts."
    return None
