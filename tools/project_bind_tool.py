"""Agent self-assignment to projects (``project_bind``) — gateway-gated.

Lets the agent bind its own conversation to a project when the user says
"let's work on the RNA analysis": the terminal/file working directory moves
into the project immediately (no conversation reset — the frozen system
prompt is untouched; the tool result tells the agent its new working dir),
and the binding becomes the chat's sticky default so future sessions in the
chat stay in the project.

Service-gated per the footprint ladder: the messaging gateway registers a
service object at startup (:func:`register_project_binding_service`); without
it the ``check_fn`` hides the tool entirely (CLI, batch, cron, TUI), so the
schema never ships to contexts that can't honor it.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

# Populated by the gateway at startup; None everywhere else. The service is a
# dict of callables (a narrow surface, not the runner):
#   status(session_key) -> dict
#   list()              -> list[dict]
#   bind(session_key, query, create=False) -> dict   (raises ValueError)
_service: Optional[dict[str, Callable[..., Any]]] = None


def register_project_binding_service(service: dict) -> None:
    """Called by the gateway once its session store and DB exist."""
    global _service
    _service = service


def clear_project_binding_service() -> None:
    global _service
    _service = None


def check_project_bind_requirements() -> bool:
    """Only exposed when a gateway registered the binding service."""
    return _service is not None


def project_bind_tool(action: str = "status", name: str = "") -> str:
    """Dispatch a project_bind call to the gateway-registered service."""
    if _service is None:
        return tool_error("project_bind is only available in gateway sessions.")

    from tools.approval import get_current_session_key

    session_key = get_current_session_key(default="")
    if not session_key:
        return tool_error("No session context — cannot resolve this conversation.")

    action = (action or "status").strip().lower()
    name = (name or "").strip()
    try:
        if action == "list":
            return json.dumps({"projects": _service["list"]()}, ensure_ascii=False)
        if action == "status":
            return json.dumps(_service["status"](session_key), ensure_ascii=False)
        if action in ("bind", "create"):
            if not name:
                return tool_error(f"action '{action}' requires a project name.")
            result = _service["bind"](session_key, name, create=(action == "create"))
            return json.dumps(result, ensure_ascii=False)
        return tool_error(
            f"Unknown action '{action}'. Use one of: status, list, bind, create."
        )
    except ValueError as e:
        return tool_error(str(e))
    except Exception as e:
        logger.warning("project_bind failed: %s", e, exc_info=True)
        return tool_error(f"project_bind failed: {e}")


PROJECT_BIND_SCHEMA = {
    "name": "project_bind",
    "description": (
        "Bind this conversation to a named project — a shared, persistent "
        "working directory that groups related sessions. Use it when the user "
        "starts or resumes a distinct workstream ('let's work on the RNA "
        "analysis'). Binding takes effect immediately: your terminal and file "
        "tools move into the project's per-session directory (returned as "
        "`workdir` — do further work there), and the chat keeps the project "
        "for future sessions. `action='bind'` attaches to an existing project "
        "(fuzzy name match), `action='create'` creates it first, `list` shows "
        "all projects, `status` shows the current binding. Do NOT create "
        "projects for one-off questions — only for named, ongoing workstreams "
        "the user intends to return to."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "list", "bind", "create"],
                "description": "What to do. Default: status.",
            },
            "name": {
                "type": "string",
                "description": (
                    "Project name or slug (required for bind/create). Fuzzy "
                    "matching applies to 'bind'."
                ),
            },
        },
    },
}

registry.register(
    name="project_bind",
    toolset="projects",
    schema=PROJECT_BIND_SCHEMA,
    handler=lambda args, **kw: project_bind_tool(
        action=args.get("action", "status"),
        name=args.get("name", ""),
    ),
    check_fn=check_project_bind_requirements,
    emoji="📁",
)
