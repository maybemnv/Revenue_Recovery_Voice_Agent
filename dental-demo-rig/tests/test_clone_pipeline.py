"""The clone pipeline: scrape parsing, extraction plumbing, and rendering.

The Claude call itself is stubbed. What is worth testing here is the wiring the
rep depends on — that a partial crawl still produces a profile, that a
schema-invalid response retries once and then fails loudly rather than shipping
a half-built agent, and that the rendered prompt always carries the three
non-negotiable clauses.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clone.extract import DEFAULT_APPOINTMENT_TYPES, ExtractionError, apply_defaults, build_prompt
from clone.kb_builder import build_agent_payload, render_knowledge_base, render_prompt, render_tools
from clone.profile import ExtractedProfile, PracticeProfile
from clone.scrape import (
    ScrapedPage,
    ScrapeResult,
    discover_links,
    extract_accent_color,
    html_to_text,
)

HOME_HTML = """
<html><head><style>a{color:#0e7c86}</style></head><body>
  <nav>
    <a href="/services">Our Services</a>
    <a href="/insurance-and-financing">Insurance</a>
    <a href="https://facebook.com/somewhere">Facebook</a>
    <a href="mailto:hi@example.com">Email</a>
    <a href="/blog/whitening-tips">Blog</a>
  </nav>
  <h1>Bright Smile Dental</h1>
  <script>var tracking = 1;</script>
  <p>Family &amp; cosmetic dentistry in Chicago.</p>
</body></html>
"""


# ---------------------------------------------------------------------------
# scrape
# ---------------------------------------------------------------------------
def test_html_to_text_drops_scripts_and_blank_lines() -> None:
    text = html_to_text(HOME_HTML)
    assert "Bright Smile Dental" in text
    assert "var tracking" not in text
    assert "\n\n" not in text


def test_discover_links_follows_only_relevant_internal_pages() -> None:
    links = discover_links(HOME_HTML, "https://example.test")
    assert "https://example.test/services" in links
    assert "https://example.test/insurance-and-financing" in links
    assert not any("facebook" in link for link in links)  # off-site
    assert not any("blog" in link for link in links)  # not profile signal


def test_accent_colour_skips_greys_and_extremes() -> None:
    assert extract_accent_color(HOME_HTML) == "#0e7c86"
    assert extract_accent_color("<style>a{color:#4a4a4a;background:#ffffff}</style>") is None


def test_combined_text_labels_each_page() -> None:
    result = ScrapeResult(
        base_url="https://example.test",
        pages=[
            ScrapedPage(url="https://example.test", text="home"),
            ScrapedPage(url="https://example.test/insurance", text="Delta Dental"),
        ],
    )
    blob = result.combined_text()
    assert "### PAGE: https://example.test/insurance" in blob
    assert "Delta Dental" in blob


def test_partial_crawl_is_flagged_to_the_model() -> None:
    """A partial crawl must not read to Claude as 'this practice offers nothing'."""
    result = ScrapeResult(
        base_url="https://example.test",
        pages=[ScrapedPage(url="https://example.test", text="home")],
        errors=["https://example.test/insurance: HTTP 500"],
        truncated=True,
    )
    prompt = build_prompt(result)
    assert "partial" in prompt.lower()
    assert "do not guess" in prompt.lower()


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------
class _StubResponse:
    def __init__(self, parsed: ExtractedProfile | None) -> None:
        self.parsed_output = parsed


class _StubClient:
    """Stands in for `anthropic.Anthropic`, counting attempts."""

    def __init__(self, *responses: object) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.messages = self

    def parse(self, **_: object) -> _StubResponse:
        self.calls += 1
        outcome = self._responses.pop(0) if self._responses else None
        if isinstance(outcome, Exception):
            raise outcome
        return _StubResponse(outcome)  # type: ignore[arg-type]


def _scrape() -> ScrapeResult:
    return ScrapeResult(
        base_url="https://example.test",
        pages=[ScrapedPage(url="https://example.test", text="Bright Smile Dental", html=HOME_HTML)],
    )


def test_extraction_returns_a_validated_profile() -> None:
    from clone.extract import extract_profile

    stub = _StubClient(ExtractedProfile(practice_name="Bright Smile Dental"))
    profile = extract_profile(_scrape(), "brightsmile", client=stub)  # type: ignore[arg-type]
    assert profile.prospect_id == "brightsmile"
    assert profile.practice_name == "Bright Smile Dental"
    assert stub.calls == 1


def test_extraction_retries_once_then_succeeds() -> None:
    from clone.extract import extract_profile

    stub = _StubClient(None, ExtractedProfile(practice_name="Bright Smile Dental"))
    profile = extract_profile(_scrape(), "brightsmile", client=stub)  # type: ignore[arg-type]
    assert stub.calls == 2
    assert profile.practice_name == "Bright Smile Dental"


def test_extraction_fails_loudly_rather_than_inventing() -> None:
    from clone.extract import extract_profile

    stub = _StubClient(None, None)
    with pytest.raises(ExtractionError):
        extract_profile(_scrape(), "brightsmile", client=stub)  # type: ignore[arg-type]
    assert stub.calls == 2


def test_extraction_refuses_an_unreadable_site() -> None:
    from clone.extract import extract_profile

    empty = ScrapeResult(base_url="https://example.test", errors=["dns failure"])
    with pytest.raises(ExtractionError, match="nothing readable"):
        extract_profile(empty, "brightsmile", client=_StubClient())  # type: ignore[arg-type]


def test_defaults_fill_what_websites_never_publish() -> None:
    profile = apply_defaults(
        PracticeProfile(prospect_id="brightsmile", practice_name="Bright Smile"), _scrape()
    )
    assert profile.appointment_types == DEFAULT_APPOINTMENT_TYPES
    assert profile.website == "https://example.test"
    assert profile.accent_color == "#0e7c86"


def test_defaults_do_not_overwrite_extracted_values() -> None:
    profile = PracticeProfile(
        prospect_id="brightsmile",
        practice_name="Bright Smile",
        accent_color="#123456",
        website="https://kept.test",
    )
    apply_defaults(profile, _scrape())
    assert profile.accent_color == "#123456"
    assert profile.website == "https://kept.test"


# ---------------------------------------------------------------------------
# profile schema
# ---------------------------------------------------------------------------
def test_profile_round_trips_through_yaml(profile: PracticeProfile) -> None:
    assert PracticeProfile.from_yaml(profile.to_yaml()) == profile


def test_bad_hours_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PracticeProfile(
            prospect_id="x1", practice_name="X", hours={"mon": "8am-5pm"}  # type: ignore[arg-type]
        )


def test_unknown_field_is_rejected() -> None:
    """Extra keys mean a hand-edited YAML has a typo the rep needs to see."""
    with pytest.raises(ValidationError):
        PracticeProfile(prospect_id="x1", practice_name="X", insurance="Delta")  # type: ignore[call-arg]


@pytest.mark.parametrize("number", ["3125550199", "+1 312 555 0199", "555-0199"])
def test_demo_number_must_be_e164(number: str) -> None:
    with pytest.raises(ValidationError):
        PracticeProfile(prospect_id="x1", practice_name="X", demo_number=number)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
REQUIRED_CLAUSES = (
    "You schedule and take intake. You never diagnose",
    "If the caller mentions facial swelling",
    "If you do not know something about this practice",
)


@pytest.mark.parametrize("clause", REQUIRED_CLAUSES)
def test_prompt_carries_every_non_negotiable_clause(
    profile: PracticeProfile, clause: str
) -> None:
    assert clause in render_prompt(profile)


def test_prompt_is_prospect_branded(profile: PracticeProfile) -> None:
    prompt = render_prompt(profile)
    assert "Bright Smile Dental" in prompt
    assert "Dr. Sarah Chen" in prompt
    assert "Melissa" in prompt


def test_kb_never_names_a_carrier_the_practice_does_not_accept(
    profile: PracticeProfile,
) -> None:
    profile.insurance_accepted = []
    kb = render_knowledge_base(profile)
    assert "Do not name any carrier as accepted" in kb


def test_tool_urls_carry_the_prospect(profile: PracticeProfile) -> None:
    tools = render_tools(profile.prospect_id, "https://tunnel.test")
    assert {t["name"] for t in tools} == {
        "check_insurance",
        "find_appointment",
        "book_appointment",
        "answer_from_kb",
    }
    assert all(t["url"].endswith(f"?prospect={profile.prospect_id}") for t in tools)


def test_transfer_tool_is_added_when_a_number_is_configured(
    profile: PracticeProfile,
) -> None:
    payload = build_agent_payload(profile, transfer_number="+13125550199")
    transfer = [t for t in payload["tools"] if t["type"] == "transfer_call"]
    assert len(transfer) == 1
    assert transfer[0]["transfer_destination"]["number"] == "+13125550199"
