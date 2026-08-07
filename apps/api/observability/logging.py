"""Structured JSON logging with call-scoped context and PII redaction.

Every log line carries whatever call context is bound to the contextvar, so
`call_id` / `client_id` / `twilio_call_sid` / `stream_sid` / `trace_id` never have
to be threaded through call signatures.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

from apps.api.security.redaction import redact_pan, redact_phone, redact_structure

_call_context: ContextVar[dict[str, Any] | None] = ContextVar("call_context", default=None)

_REDACT_PHONE_KEYS = {"from_e164", "to_e164", "phone", "phone_e164", "caller", "to", "from"}


def bind_call_context(**kwargs: Any) -> None:
    """Merge keys into the ambient log context for this task/call."""
    current = _call_context.get() or {}
    _call_context.set({**current, **{k: v for k, v in kwargs.items() if v is not None}})


def clear_call_context() -> None:
    _call_context.set({})


def get_call_context() -> dict[str, Any]:
    return dict(_call_context.get() or {})


def _inject_call_context(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key, value in (_call_context.get() or {}).items():
        event_dict.setdefault(key, value)
    return event_dict


def _redact(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Card-like digit runs never reach a log sink; phone numbers are masked.

    Nested values are walked as well. A bound `payload=` or `arguments=` dict is
    the likeliest way for caller text to reach a log line, and a top-level-only
    pass would render it verbatim inside the JSON.
    """
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            value = redact_pan(value)
            if key in _REDACT_PHONE_KEYS:
                value = redact_phone(value)
            event_dict[key] = value
        elif isinstance(value, dict | list):
            event_dict[key] = redact_structure(value)
    return event_dict


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _inject_call_context,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
