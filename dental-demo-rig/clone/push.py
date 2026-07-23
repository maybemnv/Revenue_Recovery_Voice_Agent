"""Provision a clone: Retell agent + KB + number, then the branded demo page.

Idempotent by design. A clone that already carries `retell_agent_id` is updated
in place rather than duplicated, so a rep who fixes a wrong insurance carrier and
re-pushes gets the same phone number back. Re-pushing is the normal repair path
during rehearsal, and a pipeline that provisioned a fresh number every time would
invalidate the link already sitting in a prospect's inbox.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field

from clone.kb_builder import build_agent_payload
from clone.profile import PracticeProfile
from clone.retell import ProvisionedAgent, RetellClient
from clone.settings import RIG_ROOT, get_settings


@dataclass
class PushResult:
    profile: PracticeProfile
    agent: ProvisionedAgent | None = None
    demo_page_url: str | None = None
    steps: list[str] = field(default_factory=list)
    dry_run: bool = False

    def log(self, message: str) -> None:
        self.steps.append(message)


class PushError(RuntimeError):
    pass


def push_prospect(
    profile: PracticeProfile,
    *,
    dry_run: bool = False,
    skip_deploy: bool = False,
    client: RetellClient | None = None,
) -> PushResult:
    settings = get_settings()
    result = PushResult(profile=profile, dry_run=dry_run)
    payload = build_agent_payload(profile)

    if dry_run:
        result.log("DRY RUN — nothing was sent to Retell or Vercel.")
        result.log(f"agent name: {profile.practice_name} (demo)")
        result.log(f"prompt: {len(payload['prompt'])} chars")
        result.log(f"knowledge base: {len(payload['knowledge_base'])} chars")
        result.log(
            "tools: " + ", ".join(t["name"] for t in payload["tools"])
        )
        return result

    if not settings.retell_configured:
        raise PushError("RETELL_API_KEY is not set — cannot provision. Use --dry-run to inspect.")

    owns_client = client is None
    client = client or RetellClient()
    try:
        if profile.retell_agent_id:
            agent = _update_existing(client, profile, payload, result)
        else:
            agent = _create_new(client, profile, payload, result, settings.retell_area_code)
    finally:
        if owns_client:
            client.close()

    result.agent = agent
    profile.retell_agent_id = agent.agent_id
    profile.retell_kb_id = agent.knowledge_base_id
    if agent.phone_number:
        profile.demo_number = agent.phone_number

    if not skip_deploy:
        url = deploy_demo_page(profile)
        if url:
            profile.demo_page_url = url
            result.demo_page_url = url
            result.log(f"demo page deployed: {url}")
        else:
            result.log("demo page deploy skipped (no VERCEL_TOKEN)")

    return result


def _create_new(
    client: RetellClient,
    profile: PracticeProfile,
    payload: dict,
    result: PushResult,
    area_code: int,
) -> ProvisionedAgent:
    settings = get_settings()

    kb_id = client.create_knowledge_base(profile.practice_name, payload["knowledge_base"])
    result.log(f"knowledge base created: {kb_id}")

    llm_id = client.create_llm(
        payload["prompt"], payload["tools"], payload["begin_message"], settings.retell_llm_model
    )
    result.log(f"conversation config created: {llm_id}")

    agent_id = client.create_agent(
        agent_name=f"{profile.practice_name} (demo)",
        llm_id=llm_id,
        voice_id=settings.retell_voice_id,
        webhook_url=f"{settings.webhook_base_url.rstrip('/')}/retell/post-call",
        knowledge_base_ids=[kb_id],
    )
    result.log(f"agent created: {agent_id}")

    number = client.provision_number(agent_id, area_code)
    result.log(f"number provisioned: {number}")

    return ProvisionedAgent(
        agent_id=agent_id, llm_id=llm_id, knowledge_base_id=kb_id, phone_number=number
    )


def _update_existing(
    client: RetellClient, profile: PracticeProfile, payload: dict, result: PushResult
) -> ProvisionedAgent:
    """Re-push an existing clone. Keeps the agent, the KB, and crucially the number.

    The knowledge base is replaced rather than edited: Retell has no in-place text
    update, and a stale KB is exactly the failure ("they still say they take Aetna")
    that the review gate exists to prevent.
    """
    assert profile.retell_agent_id is not None
    kb_id = client.create_knowledge_base(profile.practice_name, payload["knowledge_base"])
    result.log(f"knowledge base replaced: {kb_id}")

    llm_id = client.create_llm(
        payload["prompt"],
        payload["tools"],
        payload["begin_message"],
        get_settings().retell_llm_model,
    )
    client.update_agent(
        profile.retell_agent_id,
        {
            "response_engine": {"type": "retell-llm", "llm_id": llm_id},
            "knowledge_base_ids": [kb_id],
        },
    )
    result.log(f"agent {profile.retell_agent_id} updated (number unchanged)")

    return ProvisionedAgent(
        agent_id=profile.retell_agent_id,
        llm_id=llm_id,
        knowledge_base_id=kb_id,
        phone_number=profile.demo_number,
    )


def deploy_demo_page(profile: PracticeProfile) -> str | None:
    """Deploy the branded demo page for this prospect.

    The page is one Next.js app deployed once per prospect, with the profile
    injected as a build-time env var. Prospect-scoped preview URLs are what give
    each one a distinct link to send.
    """
    settings = get_settings()
    if not settings.vercel_token:
        return None
    web_dir = RIG_ROOT / "web"
    if not web_dir.is_dir():
        raise PushError(f"web/ not found at {web_dir}")

    vercel = shutil.which("vercel")
    if vercel is None:
        raise PushError("vercel CLI not found on PATH — `npm i -g vercel`")

    env = {
        **os.environ,
        "VERCEL_TOKEN": settings.vercel_token,
        "NEXT_PUBLIC_PROSPECT_PROFILE": json.dumps(
            profile.model_dump(mode="json", exclude_none=True)
        ),
        "NEXT_PUBLIC_SUPABASE_URL": settings.supabase_url,
        "NEXT_PUBLIC_WEBHOOK_BASE_URL": settings.webhook_base_url,
    }
    cmd = [
        vercel, "deploy", "--prod", "--yes",
        "--token", settings.vercel_token,
        "--name", f"{settings.vercel_project}-{profile.prospect_id}",
    ]
    if settings.vercel_org_id:
        cmd += ["--scope", settings.vercel_org_id]

    completed = subprocess.run(
        cmd, cwd=web_dir, env=env, capture_output=True, text=True, timeout=600
    )
    if completed.returncode != 0:
        raise PushError(f"vercel deploy failed:\n{completed.stderr[-2000:]}")
    # The CLI prints the deployment URL as the last line of stdout.
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return lines[-1] if lines else None
