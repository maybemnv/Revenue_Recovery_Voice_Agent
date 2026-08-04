"""Celery task definitions — thin wrappers over the pure functions.

Every task is a session plus a call into `analyze` / `crm_sync` / `recording`,
which keeps the retry and transaction policy in one readable place and leaves
the logic itself testable without a broker.

`analyze_call` chains into `sync_crm` on success, so the CRM gets the summary
rather than a placeholder. The chain is one-way: a CRM failure never re-runs
the analysis.
"""

from __future__ import annotations

import uuid

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from apps.api.db.session import sync_session_scope
from apps.api.observability.logging import get_logger
from apps.api.workers import analyze as analyze_mod
from apps.api.workers import crm_sync as crm_mod
from apps.api.workers import recording as recording_mod
from apps.api.workers.celery_app import app  # noqa: F401 - registers the app for `-A`

log = get_logger(__name__)

MAX_RETRIES = 3


@shared_task(bind=True, name="calls.analyze", max_retries=MAX_RETRIES)
def analyze_call(self, call_id: str) -> dict[str, object]:
    try:
        with sync_session_scope() as session:
            analysis = analyze_mod.analyze(session, uuid.UUID(call_id))
    except LookupError:
        # The call row does not exist. Retrying will not conjure it.
        log.warning("analyze_unknown_call", call_id=call_id)
        return {"status": "unknown_call"}
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:  # transient DB/API faults are retryable
        raise self.retry(exc=exc, countdown=10 * (self.request.retries + 1)) from exc

    sync_crm.delay(call_id)
    return {"status": "ok", "intent": analysis.intent, "qa_score": analysis.qa_score}


@shared_task(bind=True, name="calls.sync_crm", max_retries=MAX_RETRIES)
def sync_crm(self, call_id: str) -> dict[str, object]:
    try:
        with sync_session_scope() as session:
            crm_id = crm_mod.sync_call(session, uuid.UUID(call_id))
    except LookupError:
        log.warning("crm_sync_unknown_call", call_id=call_id)
        return {"status": "unknown_call"}
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        raise self.retry(exc=exc, countdown=30 * (self.request.retries + 1)) from exc
    return {"status": "ok", "crm_id": crm_id}


@shared_task(bind=True, name="calls.store_recording", max_retries=MAX_RETRIES)
def store_recording(
    self, call_id: str, recording_url: str, recording_sid: str = ""
) -> dict[str, object]:
    try:
        with sync_session_scope() as session:
            stored = recording_mod.store_recording(
                session,
                uuid.UUID(call_id),
                recording_url=recording_url,
                recording_sid=recording_sid,
            )
    except LookupError:
        log.warning("recording_unknown_call", call_id=call_id)
        return {"status": "unknown_call"}
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        raise self.retry(exc=exc, countdown=20 * (self.request.retries + 1)) from exc
    return {"status": "stored" if stored else "rejected_no_consent"}
