"""FastAPI: the four mock tool webhooks plus Retell's post-call webhook.

Retell posts a custom-function call as `{"call": {...}, "name": ..., "args": {...}}`.
The prospect is resolved from the `?prospect=` query parameter that `push.py`
bakes into each tool URL, with the agent ID as a fallback so a hand-edited agent
in the Retell dashboard still routes correctly.
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from clone.profile import PracticeProfile
from clone.settings import PROSPECTS_DIR
from webhooks import tools as mock_tools
from webhooks.store import generate_week, get_store

app = FastAPI(title="Dental Demo Rig — mock tools", version="1.0.0")

# The demo page is deployed per prospect to a Vercel URL we do not know ahead of
# time, and every response here is synthetic demo data.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

SHOWCASE = "_showcase"


@lru_cache(maxsize=64)
def _load_profile_cached(prospect_id: str, mtime: float) -> PracticeProfile:
    path = PROSPECTS_DIR / f"{prospect_id}.yaml"
    return PracticeProfile.from_yaml(path.read_text(encoding="utf-8"))


def load_profile(prospect_id: str) -> PracticeProfile:
    """Load a profile, re-reading it when the YAML changes on disk.

    Keyed on mtime so a rep who fixes a carrier and re-pushes sees the change on
    the next call without restarting the webhook process.
    """
    path = PROSPECTS_DIR / f"{prospect_id}.yaml"
    if not path.is_file():
        raise HTTPException(404, f"unknown prospect {prospect_id!r}")
    return _load_profile_cached(prospect_id, path.stat().st_mtime)


def _resolve_prospect(prospect: str | None, payload: dict[str, Any]) -> PracticeProfile:
    if prospect:
        return load_profile(prospect)
    agent_id = (payload.get("call") or {}).get("agent_id")
    if agent_id:
        for path in PROSPECTS_DIR.glob("*.yaml"):
            candidate = PracticeProfile.from_yaml(path.read_text(encoding="utf-8"))
            if candidate.retell_agent_id == agent_id:
                return candidate
    return load_profile(SHOWCASE)


def _args(payload: dict[str, Any]) -> dict[str, Any]:
    args = payload.get("args")
    if isinstance(args, dict):
        return args
    # Retell has shipped both shapes; accept a bare argument object too.
    return {k: v for k, v in payload.items() if k not in {"call", "name"}}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# The four mock tools
# ---------------------------------------------------------------------------
@app.post("/tools/check_insurance")
def tool_check_insurance(
    payload: dict[str, Any] = Body(default_factory=dict), prospect: str | None = Query(None)
) -> dict[str, Any]:
    profile = _resolve_prospect(prospect, payload)
    args = _args(payload)
    return _timed(lambda: mock_tools.check_insurance(profile, str(args.get("carrier", ""))))


@app.post("/tools/find_appointment")
def tool_find_appointment(
    payload: dict[str, Any] = Body(default_factory=dict), prospect: str | None = Query(None)
) -> dict[str, Any]:
    profile = _resolve_prospect(prospect, payload)
    args = _args(payload)
    return _timed(
        lambda: mock_tools.find_appointment(
            profile,
            get_store(),
            str(args.get("appointment_type", "")),
            args.get("preference"),
        )
    )


@app.post("/tools/book_appointment")
def tool_book_appointment(
    payload: dict[str, Any] = Body(default_factory=dict), prospect: str | None = Query(None)
) -> dict[str, Any]:
    profile = _resolve_prospect(prospect, payload)
    args = _args(payload)
    slot_id = str(args.get("slot_id", ""))
    if not slot_id:
        return {
            "booked": False,
            "reason_code": "missing_slot_id",
            "speak_hint": "Offer the times again and wait for the caller to choose one.",
        }
    return _timed(
        lambda: mock_tools.book_appointment(
            profile,
            get_store(),
            slot_id,
            patient_name=args.get("patient_name"),
            patient_phone=args.get("patient_phone"),
            reason=args.get("reason"),
            appointment_type=args.get("appointment_type"),
        )
    )


@app.post("/tools/answer_from_kb")
def tool_answer_from_kb(
    payload: dict[str, Any] = Body(default_factory=dict), prospect: str | None = Query(None)
) -> dict[str, Any]:
    profile = _resolve_prospect(prospect, payload)
    args = _args(payload)
    return _timed(lambda: mock_tools.answer_from_kb(profile, str(args.get("question", ""))))


def _timed(fn: Any) -> dict[str, Any]:
    started = time.perf_counter()
    result = fn()
    result["_latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


# ---------------------------------------------------------------------------
# Retell post-call webhook
# ---------------------------------------------------------------------------
@app.post("/retell/post-call")
def post_call(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, str]:
    """Persist the transcript and summary so the demo page can render them.

    Retell sends `call_started`, `call_ended`, and `call_analyzed`. Only the last
    carries the summary, so earlier events are acknowledged and dropped.
    """
    event = payload.get("event")
    call = payload.get("call") or {}
    if event not in {"call_ended", "call_analyzed"}:
        return {"status": "ignored"}

    analysis = call.get("call_analysis") or {}
    profile = _resolve_prospect(None, payload)
    get_store().record_call(
        {
            "call_id": call.get("call_id"),
            "prospect_id": profile.prospect_id,
            "agent_id": call.get("agent_id"),
            "from_number": call.get("from_number"),
            "started_at": call.get("start_timestamp"),
            "ended_at": call.get("end_timestamp"),
            "transcript": call.get("transcript"),
            "summary": analysis.get("call_summary"),
            "sentiment": analysis.get("user_sentiment"),
            "successful": analysis.get("call_successful"),
        }
    )
    return {"status": "recorded"}


# ---------------------------------------------------------------------------
# Demo-page support
# ---------------------------------------------------------------------------
@app.post("/demo/{prospect}/seed")
def seed(prospect: str, weeks: int = Query(2, ge=1, le=4)) -> dict[str, Any]:
    """Reset this prospect's calendar to a realistic week. Run before every demo."""
    profile = load_profile(prospect)
    slots = generate_week(
        profile.prospect_id,
        profile.hours.model_dump(),
        [p.model_dump() for p in profile.providers],
        profile.timezone,
        weeks=weeks,
    )
    get_store().seed(profile.prospect_id, slots)
    open_count = sum(1 for s in slots if s["status"] == "open")
    return {"prospect": prospect, "slots": len(slots), "open": open_count}


@app.post("/demo/{prospect}/web-call")
def web_call(prospect: str) -> dict[str, Any]:
    """Mint a browser-call token. Keeps the Retell API key out of the page."""
    profile = load_profile(prospect)
    if not profile.retell_agent_id:
        raise HTTPException(409, f"{prospect} has not been pushed yet")
    from clone.retell import RetellClient, RetellError

    try:
        with RetellClient() as client:
            body = client.create_web_call(profile.retell_agent_id)
    except RetellError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"access_token": body.get("access_token"), "call_id": body.get("call_id")}


@app.get("/demo/{prospect}/profile")
def profile_json(prospect: str) -> dict[str, Any]:
    """What the demo page renders its header and week grid from."""
    return load_profile(prospect).model_dump(mode="json", exclude_none=True)


@app.get("/demo/{prospect}/calls")
def calls(prospect: str, limit: int = Query(5, ge=1, le=50)) -> dict[str, Any]:
    """Recent calls, newest first, for the transcript and summary card."""
    load_profile(prospect)
    store = get_store()
    recorded = getattr(store, "calls", None)
    if recorded is None:
        raise HTTPException(
            501, "call reads come from Supabase realtime when Supabase is configured"
        )
    rows = [c for c in recorded if c["prospect_id"] == prospect]
    return {"prospect": prospect, "calls": list(reversed(rows))[:limit]}


@app.get("/demo/{prospect}/calendar")
def calendar(prospect: str) -> dict[str, Any]:
    """Every slot for the demo page's week grid, open and booked alike."""
    profile = load_profile(prospect)
    store = get_store()
    slots = getattr(store, "slots", None)
    if slots is None:
        raise HTTPException(
            501, "calendar reads come from Supabase realtime when Supabase is configured"
        )
    rows = [s for s in slots.values() if s["prospect_id"] == profile.prospect_id]
    rows.sort(key=lambda s: s["starts_at"])
    return {"prospect": prospect, "slots": rows}
