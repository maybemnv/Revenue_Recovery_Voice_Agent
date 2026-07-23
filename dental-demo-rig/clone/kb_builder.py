"""Render the templated agent prompt, knowledge base, and tool schemas.

All three are pure functions of the profile plus the webhook base URL. Nothing
here talks to a network, so the rep can inspect exactly what a clone will say
before anything is provisioned (`clone-demo preview <prospect>`).
"""

from __future__ import annotations

import json
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from clone.profile import PracticeProfile
from clone.settings import TEMPLATES_DIR, get_settings

_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)

RED_FLAG_TRANSFER_TOOL_NAME = "transfer_to_on_call"


def _context(profile: PracticeProfile) -> dict[str, Any]:
    data = profile.model_dump(mode="python")
    # Hours render as an ordered mapping so mon..sun stay in week order.
    data["hours"] = profile.hours.model_dump()
    return data


def render_prompt(profile: PracticeProfile) -> str:
    return _env.get_template("agent_prompt.md").render(**_context(profile))


def render_knowledge_base(profile: PracticeProfile) -> str:
    return _env.get_template("knowledge_base.md.j2").render(**_context(profile))


def render_tools(
    prospect_id: str | None = None, webhook_base_url: str | None = None
) -> list[dict[str, Any]]:
    """The four mock tools, with the webhook host substituted in.

    Identical across every clone. The only per-clone value is the `?prospect=`
    query parameter, which is how one webhook process serves every clone at once -
    the alternative, resolving the prospect from the agent ID, breaks the moment
    someone edits an agent by hand in the Retell dashboard.
    """
    base = (webhook_base_url or get_settings().webhook_base_url).rstrip("/")
    raw = (TEMPLATES_DIR / "tools.json").read_text(encoding="utf-8")
    schemas: list[dict[str, Any]] = json.loads(raw.replace("{{webhook_base_url}}", base))
    if prospect_id:
        for schema in schemas:
            schema["url"] = f"{schema['url']}?prospect={prospect_id}"
    return schemas


def build_transfer_tool(transfer_number: str) -> dict[str, Any]:
    """Retell's built-in call transfer, wired to the rep-controlled voicemail box.

    Kept out of `tools.json` because it is the one tool whose target is an
    environment value rather than a prospect value: every clone transfers to the
    same box the rep can answer or replay from.
    """
    return {
        "type": "transfer_call",
        "name": RED_FLAG_TRANSFER_TOOL_NAME,
        "description": (
            "Transfer the caller to the on-call line. Use this only for the red-flag "
            "symptoms named in your instructions, or when the caller asks to speak to "
            "a person. Say the transfer line first, then transfer."
        ),
        "transfer_destination": {"type": "predefined", "number": transfer_number},
        "transfer_option": {"type": "cold_transfer", "show_transferee_as_caller": False},
    }


def build_agent_payload(
    profile: PracticeProfile,
    *,
    webhook_base_url: str | None = None,
    transfer_number: str | None = None,
) -> dict[str, Any]:
    """Everything Retell needs to stand up this clone's conversation."""
    settings = get_settings()
    tools = render_tools(profile.prospect_id, webhook_base_url)
    number = transfer_number or settings.retell_transfer_number
    if number:
        tools.append(build_transfer_tool(number))
    return {
        "prompt": render_prompt(profile),
        "tools": tools,
        "knowledge_base": render_knowledge_base(profile),
        "begin_message": (
            f"Thanks for calling {profile.practice_name}, "
            "this is the after-hours line — how can I help?"
        ),
    }
