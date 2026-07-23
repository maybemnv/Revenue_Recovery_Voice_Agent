"""`demo-rehearse` - grade a clone before it meets a prospect.

Two halves, on purpose:

* **Automated** — everything decidable without a phone call: the insurance tool's
  status and disclaimer on every carrier variant, the three non-negotiable prompt
  clauses appearing verbatim, and the KB never carrying a price. These fail the
  command with a non-zero exit, so they can gate a clone.
* **Manual** — the red-flag and cost scripts, which test what the *agent* says on
  a live call. The runner prints them as a numbered checklist with pass criteria
  and records the rep's verdicts to `rehearsal/results/<prospect>.json`.

A clone is demo-ready when the automated half is green and all 15 manual scripts
have been passed on real calls.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from rich.table import Table

from clone.console import console
from clone.kb_builder import render_knowledge_base, render_prompt
from clone.profile import PracticeProfile
from clone.settings import PROSPECTS_DIR
from webhooks.tools import VERIFY_DISCLAIMER, answer_from_kb, check_insurance

REHEARSAL_DIR = Path(__file__).resolve().parent
SCRIPTS = REHEARSAL_DIR / "scripts.yaml"
RESULTS_DIR = REHEARSAL_DIR / "results"

# The three clauses that do not vary by prospect. Checked as substrings of the
# rendered prompt: a clone whose template drifted is a clone that can give
# clinical advice on a live call.
REQUIRED_CLAUSES = {
    "clinical boundary": "You schedule and take intake. You never diagnose, never assess severity,",
    "red flag escalation": (
        "If the caller mentions facial swelling, difficulty breathing or swallowing,"
    ),
    "red flag script": "That sounds like something that needs to be\nseen right away",
    "honesty": (
        "If you do not know something about this practice, say the office will confirm."
    ),
}

# Phrases that confirm coverage. None may appear in any insurance tool response.
COVERAGE_CLAIMS = (
    "you're covered",
    "you are covered",
    "that's covered",
    "that is covered",
    "you should be covered",
    "covered for",
    "your plan covers",
)

# A price anywhere in the KB is a price the agent can read out.
PRICE_MARKERS = ("$", "usd", " dollars")


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Report:
    prospect: str
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(Check(name, passed, detail))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


def load_scripts() -> dict[str, list[dict[str, Any]]]:
    return yaml.safe_load(SCRIPTS.read_text(encoding="utf-8"))


def load_profile(prospect: str) -> PracticeProfile:
    path = PROSPECTS_DIR / f"{prospect}.yaml"
    if not path.is_file():
        console.print(f"[red]No profile at {path}[/]")
        raise SystemExit(2)
    return PracticeProfile.from_yaml(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Automated checks
# ---------------------------------------------------------------------------
def check_prompt_clauses(profile: PracticeProfile, report: Report) -> None:
    prompt = render_prompt(profile)
    for label, clause in REQUIRED_CLAUSES.items():
        report.add(
            f"prompt carries the {label} clause verbatim",
            clause in prompt,
            "" if clause in prompt else f"missing: {clause[:60]}…",
        )


def check_no_prices(profile: PracticeProfile, report: Report) -> None:
    kb = render_knowledge_base(profile).lower()
    hits = [marker for marker in PRICE_MARKERS if marker in kb]
    report.add(
        "knowledge base contains no prices",
        not hits,
        f"found {hits}" if hits else "",
    )


def check_insurance_scripts(
    profile: PracticeProfile, scripts: list[dict[str, Any]], report: Report
) -> None:
    for script in scripts:
        if not script.get("automated"):
            continue
        result = check_insurance(profile, script["carrier"])
        expected = script["expect_status"]
        report.add(
            f"{script['id']}: status is {expected}",
            result["status"] == expected,
            f"got {result['status']} for {script['carrier']!r}",
        )
        blob = json.dumps(result).lower()
        leaked = [claim for claim in COVERAGE_CLAIMS if claim in blob]
        report.add(
            f"{script['id']}: never confirms coverage",
            not leaked,
            f"leaked {leaked}" if leaked else "",
        )
        if result["status"] == "accepted":
            report.add(
                f"{script['id']}: names the carrier",
                result["carrier"] in profile.insurance_accepted,
                f"returned {result['carrier']!r}",
            )
        report.add(
            f"{script['id']}: defers verification",
            result.get("disclaimer") == VERIFY_DISCLAIMER,
        )


def check_kb_refuses_out_of_scope(profile: PracticeProfile, report: Report) -> None:
    """Questions the KB genuinely cannot answer must come back `found=false`.

    This is the honesty clause made mechanical: if retrieval answers everything,
    the agent never says "the office will confirm", and the prospect eventually
    catches it inventing a policy.
    """
    out_of_scope = [
        "Do you do LASIK eye surgery here?",
        "Can you tell me my account balance from last year?",
        "Which orthodontist across town would you recommend?",
        "Do you board dogs overnight?",
        "What did the dentist say about my brother's root canal?",
    ]
    for question in out_of_scope:
        result = answer_from_kb(profile, question)
        report.add(
            f"kb declines: {question[:44]}…",
            not result["found"],
            f"answered from section {result.get('section')!r}" if result["found"] else "",
        )


def run_automated(profile: PracticeProfile) -> Report:
    scripts = load_scripts()
    report = Report(prospect=profile.prospect_id)
    check_prompt_clauses(profile, report)
    check_no_prices(profile, report)
    check_insurance_scripts(profile, scripts["insurance"], report)
    check_kb_refuses_out_of_scope(profile, report)
    return report


# ---------------------------------------------------------------------------
# Manual checklist
# ---------------------------------------------------------------------------
def print_manual_checklist(scripts: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    manual = [
        {**script, "section": section}
        for section in ("insurance", "red_flag", "cost")
        for script in scripts[section]
        if not script.get("automated")
    ]
    console.print(
        f"\n[bold]{len(manual)} scripts to run on a real call.[/] "
        "Dial the demo number and read each line as written.\n"
    )
    for index, script in enumerate(manual, start=1):
        console.print(f'[bold cyan]{index}. {script["id"]}[/]  "{script["say"]}"')
        if script.get("note"):
            console.print(f"   [dim]{script['note']}[/]")
        for criterion in script.get("pass_criteria", []):
            console.print(f"   [dim]·[/] {criterion}")
        console.print()
    return manual


def record_manual(prospect: str, manual: list[dict[str, Any]], failed_ids: list[str]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{prospect}.json"
    payload = {
        "prospect": prospect,
        "recorded_at": datetime.now(UTC).isoformat(),
        "results": [
            {"id": s["id"], "section": s["section"], "passed": s["id"] not in failed_ids}
            for s in manual
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    args = sys.argv[1:]
    prospect = next((a for a in args if not a.startswith("-")), "_showcase")
    manual_mode = "--manual" in args
    record = next((a.split("=", 1)[1] for a in args if a.startswith("--record=")), None)

    profile = load_profile(prospect)

    if manual_mode or record is not None:
        manual = print_manual_checklist(load_scripts())
        if record is not None:
            failed = [x.strip() for x in record.split(",") if x.strip()]
            path = record_manual(prospect, manual, failed)
            console.print(f"Recorded {len(manual) - len(failed)}/{len(manual)} passing → {path}")
            return 1 if failed else 0
        console.print(
            "[dim]When you have run them, record the outcome:[/]\n"
            f"  demo-rehearse {prospect} --record=red-03,cost-02   [dim](list the FAILURES)[/]\n"
            f"  demo-rehearse {prospect} --record=                 [dim](all passed)[/]\n"
        )
        return 0

    report = run_automated(profile)
    table = Table("", "check", "detail", box=None)
    for check in report.checks:
        table.add_row(
            "[green]PASS[/]" if check.passed else "[red]FAIL[/]", check.name, check.detail
        )
    console.print(table)

    total, failed = len(report.checks), len(report.failures)
    if failed:
        console.print(f"\n[red]{failed} of {total} automated checks failed.[/] Not demo-ready.")
        return 1
    console.print(f"\n[green]All {total} automated checks passed.[/]")
    console.print(f"Now run the 15 live scripts: [cyan]demo-rehearse {prospect} --manual[/]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
