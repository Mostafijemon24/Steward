"""WordPress write — deliberately narrow: the agent can only create DRAFT posts.

- Needs an admin-connected WordPress site with an Application Password (WordPress' own revocable,
  per-application credential). The credential is stored AES-GCM encrypted; only this tool, in the
  tools worker, decrypts it, right before the call. It never appears in plans, logs or outputs.
- Status "draft" is forced here; publishing, editing existing posts and deleting are impossible.
- The exact title and content are tool arguments, so the one-time approval covers exactly what
  is sent (any change invalidates the approval through the args hash).
- Requests go through the egress client: allowlisted host, public address, HTTPS, no redirects
  for POST (credentials can never be forwarded to another host).
"""

from __future__ import annotations

import base64
import json
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from sqlalchemy import select

from app.core import crypto
from app.domain.enums import StepRisk
from app.engine.egress import EgressBlocked, EgressClient, EgressFailed, workspace_policy
from app.engine.tools.base import ToolArgs, ToolContext, ToolDefinition, ToolError, ToolResult, const, task_text_class
from app.engine.tools.web_tools import _wp_dest
from app.models import IntegrationConnection, IntegrationSecret

# Allowlist (not a denylist): drafts may contain simple formatting only. Anything else is refused,
# so the approved text is exactly what is sent (nothing is silently rewritten).
ALLOWED_TAGS = {
    "p",
    "br",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "blockquote",
    "code",
    "pre",
    "a",
    "img",
    "hr",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}
ALLOWED_ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "width", "height"}}
LINK_SCHEMES = ("https://", "http://", "mailto:")
_CONTROL = {chr(c) for c in range(0x20)} | {"\x7f", " "}


def _url_ok(value: str, *, image: bool) -> bool:
    # HTMLParser has already decoded entities (&#x61; &colon; ...), like a browser would.
    v = "".join(ch for ch in value if ch not in _CONTROL).lower()
    if image:
        return v.startswith("https://")
    return v.startswith(LINK_SCHEMES) or (v.startswith(("/", "#")) and not v.startswith("//"))


class _Checker(HTMLParser):
    def __init__(self, allow_tags: bool) -> None:
        # Text keeps its entities (so "&lt;" stays text); attribute values are always decoded by
        # HTMLParser, exactly where browsers decode them too.
        super().__init__(convert_charrefs=False)
        self.allow_tags = allow_tags
        self.problem: str | None = None

    def _fail(self, why: str) -> None:
        self.problem = self.problem or why

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.allow_tags:
            return self._fail("HTML is not allowed here")
        if tag not in ALLOWED_TAGS:
            return self._fail(f"the <{tag}> tag is not allowed")
        allowed = ALLOWED_ATTRS.get(tag, set())
        for name, value in attrs:
            if name not in allowed:
                return self._fail(f"the '{name}' attribute is not allowed")
            if name in ("href", "src") and not _url_ok(value or "", image=name == "src"):
                return self._fail("only https/http/mailto links (and https images) are allowed")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if not self.allow_tags or tag not in ALLOWED_TAGS:
            self._fail(f"the </{tag}> tag is not allowed")

    def handle_data(self, data: str) -> None:
        # A "<" that did not become a complete, allowed tag (e.g. "<scr" or "< script>") is refused;
        # write "&lt;" for a literal less-than sign.
        if "<" in data:
            self._fail("unfinished or malformed HTML is not allowed (write &lt; for a literal <)")

    def handle_comment(self, data: str) -> None:
        self._fail("HTML comments are not allowed")

    def handle_decl(self, decl: str) -> None:
        self._fail("declarations are not allowed")

    def handle_pi(self, data: str) -> None:
        self._fail("processing instructions are not allowed")

    def unknown_decl(self, data: str) -> None:
        self._fail("CDATA is not allowed")


def check_draft_html(value: str, *, allow_tags: bool) -> str | None:
    """Return why the text is refused, or None if it only uses the allowed formatting."""
    if "\x00" in value:
        return "control characters are not allowed"
    checker = _Checker(allow_tags)
    checker.feed(value)
    leftover = checker.rawdata  # input the parser could not complete (e.g. a trailing "<scr")
    checker.close()
    if checker.problem:
        return checker.problem
    if "<" in leftover:
        return "unfinished or malformed HTML is not allowed (write &lt; for a literal <)"
    return None


def secret_purpose(workspace_id: object) -> str:
    return f"integration:wordpress:{workspace_id}"


class WordPressDraftArgs(ToolArgs):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20_000)
    excerpt: str = Field(default="", max_length=500)

    @field_validator("content")
    @classmethod
    def _safe_content(cls, v: str) -> str:
        why = check_draft_html(v, allow_tags=True)
        if why:
            raise ValueError(f"draft content refused: {why}")
        return v

    @field_validator("title", "excerpt")
    @classmethod
    def _plain_text(cls, v: str) -> str:
        why = check_draft_html(v, allow_tags=False)
        if why:
            raise ValueError(f"refused: {why}")
        return v


def load_credentials(ctx: ToolContext) -> tuple[str, str, str]:
    conn = ctx.db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == ctx.workspace_id, IntegrationConnection.key == "wordpress"
        )
    ).scalar_one_or_none()
    cfg = (conn.config or {}) if conn else {}
    if conn is None or conn.status != "connected" or cfg.get("mode") != "read_write_drafts":
        raise ToolError(
            "integration_not_connected",
            "WordPress is not connected with write access (Integrations → WordPress → add an Application Password).",
        )
    row = ctx.db.execute(
        select(IntegrationSecret.secret_enc).where(
            IntegrationSecret.workspace_id == ctx.workspace_id,
            IntegrationSecret.key == "wordpress",
            IntegrationSecret.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if not row:
        raise ToolError("integration_not_connected", "No WordPress credentials are stored for this workspace.")
    try:
        cred = json.loads(crypto.unseal_on_worker(row, secret_purpose(ctx.workspace_id)))
    except (crypto.DecryptionError, ValueError):
        raise ToolError("credentials_unreadable", "The stored WordPress credentials could not be decrypted.") from None
    return str(cfg["site_url"]), str(cred["username"]), str(cred["password"])


def basic_auth(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def run_create_draft(ctx: ToolContext, args: ToolArgs) -> ToolResult:
    assert isinstance(args, WordPressDraftArgs)
    site, username, password = load_credentials(ctx)
    if urlsplit(site).scheme != "https" and not ctx.settings.egress_allow_http:
        raise ToolError("insecure_site", "Credentials are only ever sent over HTTPS.")
    body = json.dumps(
        {"title": args.title, "content": args.content, "excerpt": args.excerpt, "status": "draft"}
    ).encode()
    client = EgressClient(workspace_policy(ctx.env.allowed_domains, ctx.settings), ctx.settings)
    try:
        resp = client.request(
            "POST",
            f"{site.rstrip('/')}/wp-json/wp/v2/posts",
            headers={
                "Authorization": basic_auth(username, password),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            content=body,
            max_bytes=200_000,
        )
    except EgressBlocked as e:
        raise ToolError(e.code, e.message) from None
    except EgressFailed as e:
        # Not retried automatically: creating a post is not idempotent.
        raise ToolError(e.code, e.message) from None
    if resp.status in (401, 403):
        raise ToolError("wordpress_forbidden", "WordPress refused the stored credentials (they may have been revoked).")
    if resp.status != 201:
        raise ToolError("wordpress_error", f"WordPress returned HTTP {resp.status} instead of creating the draft.")
    try:
        data: dict[str, Any] = json.loads(resp.body)
        post_id = int(data["id"])
    except (ValueError, KeyError, TypeError):
        raise ToolError("wordpress_invalid", "WordPress did not return the new draft.") from None
    if data.get("status") != "draft":
        raise ToolError(
            "wordpress_unexpected_status",
            f"WordPress saved the post with status '{str(data.get('status'))[:20]}' instead of draft. Check it now.",
        )
    edit_url = f"{site.rstrip('/')}/wp-admin/post.php?post={post_id}&action=edit"
    return ToolResult(
        summary=f"Created draft #{post_id} on {urlsplit(site).hostname} (not published)",
        output={"site": site, "post_id": post_id, "status": "draft", "edit_url": edit_url, "title": args.title},
        classification=ctx.env.task_classification,
    )


def _available(_s: Any) -> tuple[bool, str]:
    return True, ""


WORDPRESS_CREATE_DRAFT = ToolDefinition(
    id="wordpress.create_draft",
    label="WordPress: create a draft post",
    description=(
        "Creates a DRAFT post on the connected WordPress site (never publishes, edits or deletes). "
        "Needs write access set up by an admin and a one-time approval of the exact title and text."
    ),
    args_model=WordPressDraftArgs,
    risk=StepRisk.production_change,
    permissions=("integration.wordpress.write_draft",),
    impact=const("production_change"),
    classifications=task_text_class,
    destinations=_wp_dest,
    run=run_create_draft,
    setting_key="wp_write",
    idempotent=False,
    timeout_seconds=45,
    max_attempts=1,
    network=True,
    available=_available,
    args_hint={"title": "≤ 200 chars", "content": "≤ 20,000 chars, plain text or simple HTML", "excerpt": "optional"},
)
