"""The tool registry: the complete, fixed list of tools the agent can use.

A plan step may only name a tool from this registry. There is no generic shell, SQL or HTTP
tool. Tools that are not built are listed with `available=False` so the UI can say so.
"""

from __future__ import annotations

from app.core.config import Settings
from app.engine.tools.base import ToolDefinition
from app.engine.tools.files_tools import FILES_ANALYZE, FILES_READ, FILES_REQUEST_DELETION
from app.engine.tools.misc_tools import (
    AI_SUMMARIZE,
    BROWSER_AUTOMATION,
    CODE_RUN_PYTHON,
    EMAIL_SEND,
    REPORT_WRITE,
    WORDPRESS_WRITE,
)
from app.engine.tools.web_tools import SEO_AUDIT, WEB_INSPECT, WORDPRESS_READ
from app.engine.tools.wp_tools import WORDPRESS_CREATE_DRAFT

REGISTRY: dict[str, ToolDefinition] = {
    t.id: t
    for t in (
        FILES_READ,
        FILES_ANALYZE,
        WEB_INSPECT,
        SEO_AUDIT,
        WORDPRESS_READ,
        WORDPRESS_CREATE_DRAFT,
        REPORT_WRITE,
        AI_SUMMARIZE,
        CODE_RUN_PYTHON,
        FILES_REQUEST_DELETION,
        WORDPRESS_WRITE,
        EMAIL_SEND,
        BROWSER_AUTOMATION,
    )
}


def get_tool(tool_id: str) -> ToolDefinition | None:
    return REGISTRY.get(tool_id)


def tool_enabled(t: ToolDefinition, tool_permissions: dict[str, bool]) -> bool:
    return t.setting_key is None or bool(tool_permissions.get(t.setting_key, False))


def usable(t: ToolDefinition, settings: Settings, tool_permissions: dict[str, bool]) -> bool:
    return t.run is not None and t.available(settings)[0] and tool_enabled(t, tool_permissions)
