# Dental Voice Agent — Sales Demo Rig

A cloneable, prospect-branded AI receptionist demo. A rep turns a dental
practice's website into a demo agent answering as *their* practice — their name,
their insurance list, their Tuesday hours — reachable on a real phone number and
a shareable page.

**This is a sales instrument, not the product.** It shares no code with
`../apps/` (the production system in `docs/PRD.md`) and is deliberately built on
a managed voice platform with mocked tools. See [`runbook.md`](runbook.md) for
the rep-facing guide — that is the document to read if you are not an engineer.

> **Synthetic data only. No PHI. No real patient data. Ever.**
> If a prospect starts reading a real patient's details to test it, stop them.

---

## The two commands

```bash
clone-demo new https://brightsmiledental.com --prospect brightsmile
#   → scrapes the site, extracts a profile, writes prospects/brightsmile.yaml,
#     and STOPS. Review the insurance list.

clone-demo push brightsmile
#   → creates the Retell agent, uploads the KB, provisions a number,
#     deploys the branded page.
```

The gate between them is mandatory and not a nicety: scraped insurance lists are
wrong often enough that one unreviewed clone naming a carrier the practice
dropped costs more than every hour the gate ever saves.

Other commands: `clone-demo list`, `clone-demo preview <id> --what prompt|kb|tools`,
`clone-demo kill <id>` (the kill switch — takes an agent offline in one call).

---

## Setup

```bash
cd dental-demo-rig
uv sync --all-groups
cp .env.example .env      # then fill in the keys
```

Run the mock tool webhooks (Retell calls these mid-conversation):

```bash
uv run uvicorn webhooks.app:app --port 8000
ngrok http 8000           # put the https URL in WEBHOOK_BASE_URL
```

Seed a prospect's calendar before a demo — some slots full, some open:

```bash
curl -X POST http://localhost:8000/demo/_showcase/seed
```

**No accounts required to explore.** With `SUPABASE_URL` blank the calendar runs
in memory, and `clone-demo push --dry-run` prints every payload without sending
it. The test suite and the automated rehearsal checks need no credentials at all.

---

## Layout

| Path | What it is |
|---|---|
| `clone/` | The pipeline. `cli.py` is the rep's entire interface. |
| `clone/retell.py` | The only file that knows Retell's wire format. Swapping platforms moves this file and one PRD row. |
| `templates/` | `agent_prompt.md`, `knowledge_base.md.j2`, `tools.json`. Variables only — no per-prospect logic. |
| `prospects/` | One YAML per prospect. **The** tuning surface. `_showcase.yaml` is the always-live generic agent. |
| `webhooks/` | FastAPI: the four mock tools, the Retell post-call webhook, and the demo calendar store. |
| `rehearsal/` | 15 insurance + 8 red-flag + 5 cost-deflection scripts, and the runner that grades them. |
| `supabase/schema.sql` | Demo calendar and call log, with realtime enabled so a phone booking fills the on-screen slot. |
| `web/` | The branded demo page. One deploy per prospect. |

---

## Grading a clone

```bash
uv run demo-rehearse brightsmile             # automated: no phone call needed
uv run demo-rehearse brightsmile --manual    # the 15 live scripts, as a checklist
uv run demo-rehearse brightsmile --record=red-03    # record failures after calling
```

The automated half checks what is decidable without a call: every insurance
variant returns the right status and never confirms coverage, the three
non-negotiable prompt clauses appear verbatim, the KB carries no prices, and
out-of-scope questions come back "the office will confirm". It exits non-zero on
failure, so it can gate a clone.

The manual half is the two insurance pressure scripts plus all the red-flag and
cost scripts. Those test what the *agent* says on a live call, so a human runs
them.

```bash
uv run pytest        # the tool layer
uv run ruff check .
```

---

## What this demo deliberately is not

Each of these is an omission the rep must be able to say out loud without
hedging. The full list, with the exact line to say, is in
[`runbook.md`](runbook.md#the-scope-boundaries).

- **No PMS integration.** The booking writes to a demo calendar. *"In production
  it writes into your PMS — which one are you on?"* That is the discovery
  question; the absence is the hook.
- **No real patient data or PHI.** Not at any point, including live in a meeting.
- **No insurance eligibility verification.** The agent confirms the practice is
  in-network. Live benefits checks are a production integration.
- **No barge-in engineering, latency tuning, or tool-failure recovery.** That is
  the entire reason `docs/PRD.md` exists. It belongs in the pitch as a sentence,
  not in this demo as a build.

---

## Open item

`[uncertain]` Retell's BAA tier and current per-minute pricing are unresolved, as
is the exact shape of its provisioning endpoints. `clone/retell.py` is written
against the documented v2 API; verify on Day 1 with
`clone-demo push _showcase --dry-run`, which prints every payload without
spending a provisioning call.
