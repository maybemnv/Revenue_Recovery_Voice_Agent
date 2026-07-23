"""PAN and phone redaction.

Card digits must never reach the transcript, the logs, the traces, or the
post-call LLM. The agent is instructed to interrupt a caller who starts reading
digits, but the prompt is not the control - this module is.
"""

from __future__ import annotations

import re

# 13-19 digits, optionally separated by spaces or dashes. Speech-to-text renders
# spoken card numbers as space-separated groups, so the separator class matters
# more than the grouping.
_PAN_RE = re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")

# Spoken digits ("four one one one ..."), which transcription sometimes emits
# instead of numerals. Twelve or more consecutive number words is not an address.
_DIGIT_WORDS = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_SPOKEN_PAN_RE = re.compile(rf"(?i)\b(?:{_DIGIT_WORDS}[\s,\-]+){{11,}}{_DIGIT_WORDS}\b")

_CVV_RE = re.compile(r"(?i)\b(?:cvv|cvc|security code)\D{0,10}(\d{3,4})\b")

_E164_RE = re.compile(r"\+?\d{7,15}")

PAN_PLACEHOLDER = "[REDACTED_CARD]"


def _digit_count(s: str) -> int:
    return sum(c.isdigit() for c in s)


def redact_pan(text: str) -> str:
    """Replace card-number-shaped sequences with a placeholder.

    Conservative on the numeric branch: a match must hold 13-19 digits, which
    excludes phone numbers (<= 15 but normally <= 12 in practice) only when the
    caller is genuinely reading a card. Callers reading a 13+ digit run on a
    service line is card data far more often than not, so we take the false
    positive over the PCI exposure.
    """
    if not text:
        return text

    def _sub(match: re.Match[str]) -> str:
        return PAN_PLACEHOLDER if 13 <= _digit_count(match.group(0)) <= 19 else match.group(0)

    out = _PAN_RE.sub(_sub, text)
    out = _SPOKEN_PAN_RE.sub(PAN_PLACEHOLDER, out)
    return _CVV_RE.sub(lambda m: m.group(0).replace(m.group(1), "[REDACTED_CVV]"), out)


def contains_pan(text: str) -> bool:
    return redact_pan(text) != text


def redact_phone(value: str) -> str:
    """Mask all but the last four digits of anything phone-shaped."""

    def _sub(match: re.Match[str]) -> str:
        digits = match.group(0)
        if _digit_count(digits) < 7:
            return digits
        return "*" * (len(digits) - 4) + digits[-4:]

    return _E164_RE.sub(_sub, value)


def mask_e164(number: str | None) -> str | None:
    """Explicit masking for a known-phone field, e.g. `+1312555****`."""
    if not number:
        return number
    return number[:-4].ljust(len(number) - 4, "*") + "****" if len(number) > 4 else "****"
