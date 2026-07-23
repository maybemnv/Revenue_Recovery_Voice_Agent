"""Scraped site text -> `PracticeProfile`, via Claude with a strict output schema.

Schema-constrained so the CLI never has to repair JSON, and retried once because
a single malformed response should not cost the rep a re-run of the scrape.

The prompt's most important instruction is the one telling Claude to omit rather
than guess. A hallucinated insurance carrier survives the rep's review far more
easily than a missing one does - an empty list looks wrong immediately, while
"Cigna" sitting in a list of six plausible carriers does not.
"""

from __future__ import annotations

import anthropic
from pydantic import ValidationError

from clone.profile import AppointmentType, ExtractedProfile, PracticeProfile
from clone.scrape import ScrapeResult, extract_accent_color
from clone.settings import get_settings

# The standard four. Practices rarely publish appointment durations, and the demo
# needs types for `find_appointment` to reason about, so these are the fallback.
DEFAULT_APPOINTMENT_TYPES = [
    AppointmentType(name="New patient exam & x-rays", minutes=90, provider_role="dentist"),
    AppointmentType(name="Cleaning / recall", minutes=60, provider_role="hygienist"),
    AppointmentType(name="Emergency / toothache", minutes=30, provider_role="dentist"),
    AppointmentType(name="Cosmetic consult", minutes=45, provider_role="dentist"),
]

SYSTEM_PROMPT = """\
You extract a structured profile of a dental practice from the text of their \
website. The profile is used to build a demo phone agent that answers as that \
practice, and a human reviews your output before it goes live.

Rules, in priority order:

1. Only record what the website actually states. If a field is not on the site, \
omit it or leave it empty. Never infer, never fill from what is typical for a \
dental practice, and never carry a carrier or service over because it "usually" \
appears alongside one that is stated.
2. Insurance carriers are the highest-risk field. List a carrier only if the \
site names it as accepted or in-network. Do not include a carrier that appears \
only in a sentence about not accepting it, about filing out-of-network claims, \
or in a generic "we work with most major insurers" line.
3. Provider names: include the credential-free display name as a patient would \
say it ("Dr. Sarah Chen", "Melissa"). Classify hygienists as `hygienist`. If \
the site does not say whether they accept new patients, leave `accepts_new` true.
4. Hours: convert to 24-hour "HH:MM-HH:MM" per day, or "closed". A day the site \
does not mention is closed.
5. `tone`: one short sentence describing how this practice's own copy sounds, so \
the agent can match it. Base it on their writing, not on their specialty.
6. `timezone`: an IANA identifier inferred from the practice's stated city and \
state. This is the one field where inference is expected.
7. `appointment_types`: only include these if the site states specific visit \
types with durations. Otherwise leave empty - a sensible default is applied later.
"""

USER_TEMPLATE = """\
Website: {base_url}
{note}
Extract the practice profile from the following page text.

{body}
"""


class ExtractionError(RuntimeError):
    pass


def build_prompt(scrape: ScrapeResult) -> str:
    note = ""
    if scrape.truncated or scrape.errors:
        note = (
            "\nNote: the crawl was partial. Some pages were unreachable, so absent "
            "information means 'not captured', not 'not offered'. Still do not guess.\n"
        )
    return USER_TEMPLATE.format(
        base_url=scrape.base_url, note=note, body=scrape.combined_text()
    )


def extract_profile(
    scrape: ScrapeResult,
    prospect_id: str,
    *,
    client: anthropic.Anthropic | None = None,
    max_attempts: int = 2,
) -> PracticeProfile:
    """Run the extraction and return a validated profile.

    Raises `ExtractionError` if the model cannot produce a schema-valid profile
    within `max_attempts`, which is the signal for the rep to fill the YAML by
    hand rather than for the CLI to invent one.
    """
    if not scrape.ok:
        raise ExtractionError(
            f"nothing readable at {scrape.base_url}: {'; '.join(scrape.errors) or 'no pages'}"
        )

    settings = get_settings()
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    prompt = build_prompt(scrape)

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.messages.parse(
                model=settings.extraction_model,
                max_tokens=8000,
                thinking={"type": "adaptive"},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                output_format=ExtractedProfile,
            )
            extracted = response.parsed_output
            if extracted is None:
                raise ExtractionError("model returned no parsed output")
            profile = extracted.into_profile(prospect_id)
            return apply_defaults(profile, scrape)
        except (ValidationError, ExtractionError, anthropic.APIStatusError) as exc:
            last_error = exc
            if attempt == max_attempts:
                break
    raise ExtractionError(
        f"could not extract a valid profile after {max_attempts} attempts: {last_error}"
    ) from last_error


def apply_defaults(profile: PracticeProfile, scrape: ScrapeResult | None = None) -> PracticeProfile:
    """Fill the fields the demo needs but a website rarely publishes."""
    if not profile.appointment_types:
        profile.appointment_types = list(DEFAULT_APPOINTMENT_TYPES)
    if not profile.website and scrape is not None:
        profile.website = scrape.base_url
    if not profile.accent_color and scrape is not None and scrape.pages:
        profile.accent_color = extract_accent_color(scrape.pages[0].html)
    return profile
