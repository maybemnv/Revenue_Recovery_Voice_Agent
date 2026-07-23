"""Thin Retell API client - the only file that knows Retell's wire format.

Kept deliberately thin and in one place. The platform decision is one row in the
PRD's stack table, and if it changes (Vapi, ElevenLabs Agents, Synthflow), this
file and the stack row are what move. Nothing else in the rig imports `httpx`
against a voice platform.

`[uncertain]` Endpoint paths and payload shapes are written against Retell's v2
API as documented. Verify against the account on Day 1 - the `--dry-run` flag on
`clone-demo push` prints every payload without sending it, which is the cheapest
way to check shapes before spending a provisioning call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from clone.settings import get_settings


class RetellError(RuntimeError):
    def __init__(self, action: str, status: int, body: str) -> None:
        super().__init__(f"retell {action} failed: HTTP {status}: {body[:400]}")
        self.action = action
        self.status = status
        self.body = body


@dataclass
class ProvisionedAgent:
    agent_id: str
    llm_id: str
    knowledge_base_id: str | None
    phone_number: str | None


class RetellClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.retell_api_key
        self._base = (base_url or settings.retell_api_base).rstrip("/")
        self._client = client or httpx.Client(timeout=30.0)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RetellClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _post(self, path: str, payload: dict[str, Any], action: str) -> dict[str, Any]:
        response = self._client.post(
            f"{self._base}{path}",
            json=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        if response.status_code >= 400:
            raise RetellError(action, response.status_code, response.text)
        return response.json() if response.content else {}

    def _patch(self, path: str, payload: dict[str, Any], action: str) -> dict[str, Any]:
        response = self._client.patch(
            f"{self._base}{path}",
            json=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        if response.status_code >= 400:
            raise RetellError(action, response.status_code, response.text)
        return response.json() if response.content else {}

    # -- LLM / conversation config ----------------------------------------
    def create_llm(
        self, prompt: str, tools: list[dict[str, Any]], begin_message: str, model: str
    ) -> str:
        body = self._post(
            "/create-retell-llm",
            {
                "model": model,
                "general_prompt": prompt,
                "general_tools": tools,
                "begin_message": begin_message,
            },
            "create-retell-llm",
        )
        return str(body["llm_id"])

    def update_llm(
        self,
        llm_id: str,
        *,
        prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        begin_message: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {}
        if prompt is not None:
            payload["general_prompt"] = prompt
        if tools is not None:
            payload["general_tools"] = tools
        if begin_message is not None:
            payload["begin_message"] = begin_message
        if payload:
            self._patch(f"/update-retell-llm/{llm_id}", payload, "update-retell-llm")

    # -- Knowledge base ----------------------------------------------------
    def create_knowledge_base(self, name: str, markdown: str) -> str:
        body = self._post(
            "/create-knowledge-base",
            {
                "knowledge_base_name": name,
                "knowledge_base_texts": [{"title": f"{name} practice information",
                                          "text": markdown}],
            },
            "create-knowledge-base",
        )
        return str(body["knowledge_base_id"])

    # -- Agent -------------------------------------------------------------
    def create_agent(
        self,
        *,
        agent_name: str,
        llm_id: str,
        voice_id: str,
        webhook_url: str | None = None,
        knowledge_base_ids: list[str] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "agent_name": agent_name,
            "response_engine": {"type": "retell-llm", "llm_id": llm_id},
            "voice_id": voice_id,
            "language": "en-US",
        }
        if webhook_url:
            payload["webhook_url"] = webhook_url
        if knowledge_base_ids:
            payload["knowledge_base_ids"] = knowledge_base_ids
        body = self._post("/create-agent", payload, "create-agent")
        return str(body["agent_id"])

    def update_agent(self, agent_id: str, payload: dict[str, Any]) -> None:
        self._patch(f"/update-agent/{agent_id}", payload, "update-agent")

    # -- Web call ----------------------------------------------------------
    def create_web_call(self, agent_id: str) -> dict[str, Any]:
        """Mint a short-lived access token for a browser call to this agent.

        The web-call button is what survives a Zoom screenshare when the prospect
        will not dial from their desk phone. The token is minted server-side so
        the Retell API key never reaches the browser.
        """
        return self._post("/v2/create-web-call", {"agent_id": agent_id}, "create-web-call")

    # -- Phone number ------------------------------------------------------
    def provision_number(self, agent_id: str, area_code: int) -> str:
        body = self._post(
            "/create-phone-number",
            {"area_code": area_code, "inbound_agent_id": agent_id},
            "create-phone-number",
        )
        return str(body["phone_number"])

    def release_number(self, phone_number: str) -> None:
        response = self._client.delete(
            f"{self._base}/delete-phone-number/{phone_number}",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        if response.status_code >= 400:
            raise RetellError("delete-phone-number", response.status_code, response.text)

    # -- Kill switch -------------------------------------------------------
    def take_offline(self, phone_number: str) -> None:
        """Unbind the number from its agent. The number stops answering as the agent.

        This is the runbook's kill switch: it is one call, reversible, and does
        not delete the agent or its transcripts.
        """
        self._patch(
            f"/update-phone-number/{phone_number}",
            {"inbound_agent_id": None},
            "update-phone-number",
        )
