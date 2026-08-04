"""The Celery application.

Workers run on the sync engine, deliberately. The media plane is async because
it is I/O-bound on two sockets at once; a post-call worker is a queue consumer
where async buys nothing and costs an event loop per process.

`acks_late` plus `reject_on_worker_lost` means a task killed mid-flight is
redelivered rather than lost. Every task below is written to be safe under that
redelivery — analysis upserts, CRM sync dedupes on E.164.
"""

from __future__ import annotations

from celery import Celery

from apps.api.observability.logging import configure_logging
from apps.api.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)

app = Celery(
    "voice_agent", broker=settings.celery_broker_url, backend=settings.celery_result_backend
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=300,
    task_soft_time_limit=240,
    result_expires=3600,
    task_default_retry_delay=10,
    imports=("apps.api.workers.tasks",),
)
