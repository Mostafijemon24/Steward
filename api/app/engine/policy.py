"""Policy engine: default deny.

Every plan step is evaluated when the plan is built and again right before execution.
Inputs are server-side facts only: the tool registry entry, validated arguments, the files'
stored classifications, the workspace's privacy mode, tool switches, allowed domains and the
configured model provider. Workspace settings can make the policy stricter, never weaker than
the baseline rules below.

Baseline (cannot be turned off):
- Unknown or unavailable tools, and tools switched off in settings, are denied.
- Network access only to allowlisted domains; anything else is denied.
- Data classified `unknown` never leaves the workspace.
- `confidential` data leaves only with a one-time approval.
- Publishing, sending messages, production changes, permission changes and code execution
  always need a one-time approval. Prepare mode never applies changes.
- Deletion happens only through the deletion workflow (request → approval → re-validation →
  deletion service); no tool deletes directly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from app.core.config import Settings
from app.domain.enums import ApprovalCategory, DataClass, PrivacyMode
from app.engine.classification import most_sensitive
from app.engine.egress import host_allowed
from app.engine.tools import tool_enabled
from app.engine.tools.base import PlanEnv, ToolArgs, ToolDefinition

POLICY_VERSION = "p5-2026-10-24.1"

Outcome = Literal["allow", "require_approval", "deny"]

ALWAYS_APPROVE = {
    "publish": ApprovalCategory.publish,
    "send_message": ApprovalCategory.send_message,
    "production_change": ApprovalCategory.production_change,
    "permission_change": ApprovalCategory.permission_change,
    "sensitive_change": ApprovalCategory.code_execution,
}


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reasons: list[str]
    approval_category: str | None = None
    data_leaves: bool = False
    classification: str = DataClass.public.value
    version: str = POLICY_VERSION
    rules: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _deny(reason: str, rule: str, cls: str) -> Decision:
    return Decision("deny", [reason], classification=cls, rules=[rule])


def evaluate_model_transfer(env: PlanEnv, classification: DataClass, what: str) -> Decision:
    """Data sent to the configured model provider. Local providers keep data on your servers."""
    cls = classification.value
    if not env.provider.configured:
        return _deny("No AI model is configured.", "model.not_configured", cls)
    if env.provider.location == "local":
        return Decision(
            "allow",
            [f"{what} is processed by a model on your own infrastructure."],
            classification=cls,
            rules=["model.local"],
        )
    if classification == DataClass.unknown:
        return _deny("Unclassified data never leaves the workspace.", "class.unknown_never_leaves", cls)
    if not env.tool_permissions.get("external", False):
        return _deny(
            "Sending data to external services is turned off in Settings → Tool permissions.",
            "setting.external_off",
            cls,
        )
    reasons = [f"{what} would be sent to {env.provider.label}, outside your servers."]
    rules = ["model.external"]
    if classification == DataClass.confidential:
        reasons.append("Confidential data needs a one-time approval before it leaves.")
        rules.append("class.confidential_needs_approval")
        return Decision("require_approval", reasons, ApprovalCategory.external_transfer.value, True, cls, rules=rules)
    mode = PrivacyMode(env.privacy_mode)
    if mode != PrivacyMode.custom or env.ai_rules.get("share", True):
        reasons.append(
            "Strict and approval privacy modes ask before any data leaves."
            if mode != PrivacyMode.custom
            else "Your AI rules ask before sharing data externally."
        )
        rules.append(f"mode.{mode.value}_asks")
        return Decision("require_approval", reasons, ApprovalCategory.external_transfer.value, True, cls, rules=rules)
    reasons.append(f"{classification.value.capitalize()} data may go to the configured provider in custom mode.")
    return Decision("allow", reasons, None, True, cls, rules=[*rules, "mode.custom_allows"])


def evaluate_step(
    tool: ToolDefinition | None,
    args: ToolArgs | None,
    env: PlanEnv,
    settings: Settings,
    task_mode: str,
) -> Decision:
    if tool is None or args is None:
        return _deny("This tool does not exist in the tool registry.", "tool.unknown", DataClass.unknown.value)
    impact = tool.impact(args, env)
    classes = tool.classifications(args, env)
    cls = most_sensitive(classes)
    destinations = tool.destinations(args, env)

    if tool.run is None:
        return _deny(tool.description, "tool.not_built", cls.value)
    ok, why = tool.available(settings)
    if not ok:
        return _deny(why, "tool.unavailable", cls.value)
    if not tool_enabled(tool, env.tool_permissions):
        return _deny(f"“{tool.label}” is turned off in Settings → Tool permissions.", "setting.tool_off", cls.value)

    if impact == "delete":
        if tool.id != "files.request_deletion":
            return _deny("Deletion only happens through the deletion workflow.", "delete.workflow_only", cls.value)
        return Decision(
            "allow",
            [
                "This step only creates a deletion request. Deleting needs its own approval and runs in the deletion service."
            ],
            classification=cls.value,
            rules=["delete.request_only"],
        )

    if tool.network:
        # Checked before the approval rules: an approval can never widen the allowlist.
        if not destinations:
            return _deny("No destination for a network tool.", "network.no_destination", cls.value)
        if not env.allowed_domains:
            return _deny(
                "No allowed domains are set for this workspace (Settings → Allowed domains).",
                "network.no_allowlist",
                cls.value,
            )
        for d in destinations:
            if not host_allowed(d, env.allowed_domains):
                return _deny(
                    f"{d} is not on this workspace's allowed domains list.", "network.domain_not_allowed", cls.value
                )

    if impact in ALWAYS_APPROVE:
        if task_mode == "prepare" and impact != "sensitive_change":
            return _deny("Prepare mode never applies changes.", "mode.prepare_no_changes", cls.value)
        leaves = impact in ("publish", "send_message") or tool.network
        if leaves and cls == DataClass.unknown:
            return _deny("Unclassified data never leaves the workspace.", "class.unknown_never_leaves", cls.value)
        cat = ALWAYS_APPROVE[impact]
        return Decision(
            "require_approval",
            [f"{tool.label} always needs a one-time approval."]
            + ([f"The approved content is sent to {', '.join(destinations)}."] if tool.network else []),
            cat.value,
            leaves,
            cls.value,
            rules=[f"impact.{impact}_always_approve"],
        )

    if impact == "external_read":
        if PrivacyMode(env.privacy_mode) == PrivacyMode.strict:
            return Decision(
                "require_approval",
                [
                    f"Strict privacy mode asks before contacting {', '.join(destinations)} (the request reveals the address being read)."
                ],
                ApprovalCategory.external_transfer.value,
                True,
                cls.value,
                rules=["mode.strict_external_read"],
            )
        return Decision(
            "allow",
            [f"Read-only request to an allowed domain: {', '.join(destinations)}."],
            classification=cls.value,
            rules=["network.allowlisted_read"],
        )

    if impact == "external_transfer":
        return evaluate_model_transfer(env, cls, "Results from earlier steps")

    if impact in ("none", "workspace_write"):
        return Decision(
            "allow",
            ["Stays inside this workspace."],
            classification=cls.value,
            rules=[f"impact.{impact}"],
        )

    return _deny("No policy rule allows this action.", "default.deny", cls.value)
