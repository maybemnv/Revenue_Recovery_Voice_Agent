# Revenue Recovery Voice Agent — Road to Client Demo

**Goal:** a live phone number a client can dial, hold a real conversation with, interrupt mid-sentence, and walk away booked — with a dashboard replaying exactly what happened.

| Field | Value |
|---|---|
| Owner | Manav |
| Baseline commit | `6a83972` |
| Baseline date | 2026-07-31 |
| Plan written | 2026-08-03 |
| Source of truth | `docs/PRD.md` |
| Supersedes | `todo.md` (kept as the exhaustive production backlog; this file is the ordered path to demo) |
| Suggested demo target | 2026-09-07 (5 working weeks) |

---

## How to read this file

Every item carries a priority tag. Do not start a lower tag while a higher one is open in the same track.

| Tag | Meaning |
|---|---|
| **[D0]** | Demo-blocking. Without it there is no demo. |
| **[D1]** | Demo-quality. The demo runs without it but lands badly. |
| **[P]** | Production hardening. Required before a paying client's real phone line, not before the demo. |

Every item states its **file**, its **done-when**, and where useful the **command** that proves it. An item is not checked until the command has been run and its output seen. A green checkbox with no evidence behind it is how a demo dies on speakerphone.

---

## Ground Truth: what actually exists today

Updated against `28da055` after the ordered Track 0-6 commits. The original
baseline description below has been replaced with the current implementation
state; external-provider and live-phone checks remain deliberately separate.

### Real, working, reusable — keep all of it

| File | Lines | What it gives you |
|---|---|---|
| `apps/api/config/schema.py` | 141 | Full `ClientConfig` Pydantic model, `extra="forbid"`, E.164 + tz + time-range validators |
| `apps/api/config/loader.py` | 104 | mtime-driven hot-reload registry, `resolve_by_number()` for Twilio `To` routing |
| `apps/api/settings.py` | 95 | Every env var typed, `websocket_base_url` scheme-swap property |
| `apps/api/observability/logging.py` | 83 | structlog with call-scoped context |
| `apps/api/security/redaction.py` | 73 | PAN + phone redaction |
| `apps/api/db/session.py` | 64 | async + sync session factories |
| `config/clients/northside-hvac.yaml` | 39 | Reference client config |

That foundation is now extended by the database, media, tools, resilience,
worker, dashboard, and eval commits listed above. The repository currently has
the following verified implementation surfaces:

- Seven SQLAlchemy tables, pgvector and table/index migrations, repository helpers, and FastAPI health/readiness wiring.
- TwiML inbound routing, UUID call creation, `/media/{call_id}`, GA Realtime session construction, passthrough bridge, playback ledger, barge-in ordering, and budget guard.
- Six per-call tools, typed failure envelopes, latency masking, Cal.com/HubSpot/Twilio/Stripe adapter boundaries, KB ingestion, and pure domain functions.
- Deterministic escalation, missed-call SMS, STOP suppression, consent gating, PAN redaction, Celery workers, REST/SSE routers, and bearer-token dashboard auth.
- Next.js dashboard routes for calls, detail replay, live SSE, agent configuration, and analytics empty states.
- Forty YAML eval scenarios, hard graders, a committed baseline, and 110 unit tests.

### Known-broken, fix in Track 0

- [x] **[D0]** `pyproject.toml` `kb-ingest` points at a non-existent module — `apps/api/cli/kb_ingest.py` now exists
- [x] **[D0]** `pyproject.toml` `eval-run` points at an empty module — `apps/eval/runner.py` now exists
- [x] **[D1]** `docker-compose.yml` is 4 lines and references a Dockerfile that doesn't exist — Dockerfile and expanded compose now exist; live `docker compose up` remains unverified

---

## The Demo Narrative — write this before writing code

Everything in this plan exists to make one 6-minute story work on a speakerphone. Define the story first; it is the only reliable filter for what to cut when the schedule slips.

- [x] **[D0]** Write `docs/DEMO_SCRIPT.md` with the exact beats below and the exact words you will say between them
- [x] **[D0]** Confirm each beat maps to a checklist track, and that no beat depends on a **[P]** item

| # | Beat | What the client sees | Depends on |
|---|---|---|---|
| 1 | You dial the number on speakerphone | Agent answers in ≤ 6 s, by name, in a natural voice | Track 1 |
| 2 | "My AC died, I'm at 2119 N Halsted" | Agent confirms service area with no dead air | Track 2 |
| 3 | **You interrupt it mid-sentence** | Agent stops within ~200 ms and responds to what you *actually* said | Track 1 |
| 4 | "Yes, book it" | Real appointment on a real calendar, confirmation SMS to your phone | Track 2 |
| 5 | You kill Cal.com in a terminal, call again | Agent promises a callback — never claims a fake booking | Track 3 |
| 6 | You say "I smell gas" | Immediate transfer, no model deliberation | Track 3 |
| 7 | You open the dashboard | Full transcript, inline tool chips, barge-in marker, per-turn latency, cost | Track 5 |

Beat 3 and beat 5 are the demo. Beat 3 is the thing no vendor-wrapper competitor can show, and beat 5 is the thing every buyer has been burned by. If the schedule collapses, cut beats 4, 6, and 7 before you cut 3 or 5.

---

## Track 0 — Foundation (Week 1, days 1–2)

Nothing else can start until the app boots, the DB has tables, and tests can run.

### Accounts and credentials — do these first, some have lead time

- [ ] **[D0]** Twilio account funded; a voice-capable number purchased in the demo's area code
- [ ] **[D0]** Twilio number's Voice webhook pointed at `{PUBLIC_BASE_URL}/twiml/incoming` (POST)
- [ ] **[D0]** OpenAI account with **Realtime API access confirmed** — place one throwaway session before Week 1 ends
- [ ] **[D0]** Verify `gpt-realtime-2.1` (pinned in `config/clients/northside-hvac.yaml`) is a model ID your key can actually reach; correct the YAML if the GA name differs
- [ ] **[D0]** ngrok (or Cloudflare Tunnel) reserved domain so `PUBLIC_BASE_URL` is stable across restarts — a rotating URL means re-pointing Twilio every boot
- [ ] **[D1]** Anthropic API key for post-call analysis
- [ ] **[D1]** Cal.com account + event type created; note the numeric `CALCOM_EVENT_TYPE_ID`
- [ ] **[D1]** HubSpot free-tier portal + private-app token
- [ ] **[P]** Stripe test-mode key and a price object
- [ ] **[P]** Sentry project DSN
- [ ] **[P]** Langfuse instance (self-hosted per PRD — client transcripts are PII)

> **Lead-time risk:** OpenAI Realtime access and Twilio number provisioning are the two that can silently block a week. Prove both on day 1 with a hello-world, not on day 4 when the media plane is ready for them.

### Repo hygiene

- [x] **[D0]** `mkdir tests/` with `conftest.py`; `uv run pytest` exits 0 on an empty suite instead of erroring on a missing `testpaths`
- [x] **[D0]** Resolve the two broken `[project.scripts]` entry points (see Known-broken above)
- [ ] **[D0]** `.env` created from `.env.example` with real values; confirm `.gitignore` covers it
- [x] **[D0]** `uv sync` succeeds and `uv run python -c "from apps.api.settings import get_settings; print(get_settings().environment)"` prints `local`
- [ ] **[D1]** `Dockerfile` for the API; `docker compose up` brings api + postgres + redis — Dockerfile exists; service startup is not yet verified in this environment
- [x] **[D1]** `docker-compose.yml` expanded to api, worker, web, postgres (pgvector image), redis
- [x] **[D1]** `README.md` written — it is 0 bytes today and it is the first thing anyone opening the repo reads
- [x] **[D1]** `uv run ruff check .` and `uv run mypy apps` both clean; wire into a pre-commit hook
- [ ] **[P]** GitHub Actions: ruff + mypy + pytest + docker build on every push

### Database

- [x] **[D0]** Write `apps/api/db/models.py` — all 7 tables from `docs/PRD.md:478-549`: `calls`, `turns`, `tool_invocations`, `call_events`, `contacts`, `call_analyses`, `kb_chunks`
- [x] **[D0]** Preserve the constraints as *constraints*, not application logic — especially `contacts UNIQUE (client_id, phone_e164)`, which the PRD calls out as the fix for a real two-worker race
- [x] **[D0]** `alembic init apps/api/db/migrations`; configure `env.py` against `DATABASE_URL_SYNC`
- [x] **[D0]** First migration creates all tables; a separate early migration runs `CREATE EXTENSION IF NOT EXISTS vector`
- [x] **[D0]** `kb_chunks.embedding` is `VECTOR(1536)` and matches `OPENAI_EMBEDDING_MODEL=text-embedding-3-small`
- [x] **[D1]** HNSW index on `kb_chunks USING hnsw (embedding vector_cosine_ops)`
- [x] **[D1]** Indexes for the dashboard's real queries: `calls(client_id, started_at DESC)`, `turns(call_id, started_at_ms)`, `call_events(call_id, at_ms)`
- [ ] **[D0]** `alembic upgrade head` on a clean DB succeeds; `\dt` in psql shows 7 tables

**Done-when:** `uv run uvicorn apps.api.main:app --reload` boots, `/health` returns 200 with Postgres and Redis both reporting reachable.

---

## Track 1 — Media Plane (Week 1, days 3–5 → Week 2)

This is the project. Beat 3 of the demo lives entirely here.

### Inbound call path

- [x] **[D0]** `telephony/twiml.py` — `POST /twiml/incoming` resolves Twilio `To` → `ClientConfig` via the **already-built** `get_registry().resolve_by_number()`
- [x] **[D0]** Returns `<Say>` consent preamble, then `<Connect><Stream url="wss://.../media/{call_id}">`
- [x] **[D0]** `call_id` (UUID) minted here and a `calls` row inserted before the stream opens
- [x] **[D1]** Unknown `To` number returns a graceful spoken message, not a 500 — a stack trace to a caller is a bad look even in dev
- [x] **[P]** Twilio signature validation gated on `TWILIO_VALIDATE_SIGNATURES` (already in `settings.py`)
- [ ] **[D0]** Test: a real phone call reaches the endpoint and you hear the consent line

### Twilio WebSocket gateway

- [x] **[D0]** `media/gateway.py` — `WS /media/{call_id}`, one instance per call
- [x] **[D0]** Parse all five Twilio events: `connected`, `start`, `media`, `mark`, `stop`
- [x] **[D0]** Capture `streamSid` from `start` — every outbound `media` and `clear` needs it
- [ ] **[D1]** `stop` closes both sockets, flushes final `turns`, enqueues the post-call chain
- [ ] **[P]** Graceful shutdown drains active calls instead of dropping them mid-sentence
- [ ] **[P]** Bounded outbound queue so a slow socket cannot grow memory without limit

### OpenAI Realtime client

- [x] **[D0]** `media/realtime_client.py` — WSS connect to `OPENAI_REALTIME_URL`
- [x] **[D0]** **Do not send the `OpenAI-Beta: realtime=v1` header.** GA rejects the beta shapes and most public tutorials are still written against them (`docs/PRD.md:298`)
- [x] **[D0]** `session.update` uses the nested GA shape from `docs/PRD.md:300-327`: `session.type`, `session.audio.input.format`, `session.audio.output.format` — **not** flat `input_audio_format`
- [x] **[D0]** Both formats set to `{"type": "audio/pcmu"}`
- [x] **[D0]** `turn_detection: {type: "semantic_vad", interrupt_response: true}` sourced from client YAML
- [x] **[D0]** Stored prompt by `prompt_id` + pinned `prompt_version` + `variables`; `schema.py` already validates that one of `prompt_id`/`instructions` is present
- [x] **[D0]** Handle GA event names: `response.output_audio.delta`, `input_audio_buffer.speech_started`, `input_audio_buffer.speech_stopped`, `response.cancelled`
- [ ] **[D0]** Integration test asserts `session.update` → `session.updated` round-trips with `audio/pcmu` both directions

### The relay

- [x] **[D0]** `media/bridge.py` — two asyncio tasks, Twilio→OpenAI and OpenAI→Twilio
- [x] **[D0]** Twilio `media.payload` (base64 μ-law) forwarded as `input_audio_buffer.append` **byte-identical** — no decode, no resample, no re-encode
- [x] **[D0]** `response.output_audio.delta` forwarded back as a Twilio `media` event, equally untouched
- [x] **[D0]** A `mark` with a monotonic sequence number sent after **every** outbound audio chunk
- [x] **[D0]** Assert in a test that the base64 string in equals the base64 string out — transcoding is the bug class this whole design exists to avoid
- [ ] **[D0]** A real call produces intelligible two-way audio in both directions

### Playback ledger — the truncation source of truth

- [x] **[D0]** `media/playback_ledger.py` per the reference implementation at `docs/PRD.md:337-361`
- [x] **[D0]** `on_chunk_sent(mark_name, payload_bytes)` accumulates; `on_mark_ack(mark_name)` advances `_played_bytes`
- [x] **[D0]** `played_ms_for_current_item()` converts at `ULAW_BYTES_PER_SECOND = 8000`
- [x] **[D0]** `current_item_id` and `item_start_offset` tracked so truncation is per-item, not per-call
- [x] **[D0]** Unit test: **in-order** acks → correct `played_ms` within ±20 ms
- [x] **[D0]** Unit test: **out-of-order** acks
- [x] **[D0]** Unit test: **dropped/missing** acks
- [x] **[D0]** Unit test: **duplicate** acks are idempotent
- [x] **[D0]** Unit test: **late** acks arriving after the item ended do not corrupt the next item's offset
- [ ] **[P]** Ledger state mirrored to Redis so a media-worker restart mid-call is survivable

> These five ack tests are the highest value-per-line tests in the project. Every ack ordering is something Twilio will actually do to you under carrier jitter, and each one silently produces a wrong `audio_end_ms` — which desyncs the model for the rest of the call. Write them before the barge-in controller, not after.

### Barge-in controller

- [x] **[D0]** `media/barge_in.py` per `docs/PRD.md:366-389`
- [x] **[D0]** Read `audio_end_ms` from the ledger **before** sending anything
- [x] **[D0]** Order, exactly: **1.** Twilio `clear` → **2.** OpenAI `response.cancel` → **3.** OpenAI `conversation.item.truncate`
- [x] **[D0]** Test asserts the send order explicitly, not just that all three fired
- [x] **[D0]** Test asserts `audio_end_ms` in the truncate matches ledger state at cut time
- [x] **[D0]** `truncated_at_ms` persisted to the `turns` row
- [x] **[D0]** `call_events` row of kind `barge_in` emitted
- [x] **[D1]** `ledger.begin_new_item()` called so the next item's offset is clean
- [x] **[D1]** Guard the race where `speech_started` arrives with no in-flight item — must not throw

> `clear` goes first because the buffered audio is what the caller is talking over. Every millisecond it keeps playing is the agent talking over the customer. This ordering is the single most reviewable detail in the codebase — a reviewer who knows voice AI will check it first.

### Instrumentation

- [x] **[D0]** Per turn, measure `input_audio_buffer.speech_stopped` → first outbound Twilio `media` byte; persist to `turns.latency_ms`
- [x] **[D0]** Measure barge-in cut-off: `speech_started` → last audio byte queued
- [x] **[D1]** `media/budget_guard.py` — accumulate per-call cost and duration
- [x] **[D1]** Soft wrap-up prompt injected at `budget.wrap_up_at_pct` (80%)
- [x] **[D1]** Hard graceful close at `max_call_cost_usd` / `max_call_seconds`
- [ ] **[D1]** Verify by forcing `max_call_cost_usd` absurdly low and confirming a *graceful* close, not a dropped socket
- [ ] **[D0]** `calls` and `turns` rows written for every call; transcript readable from psql

### Resolve two PRD uncertainties

- [ ] **[D1]** Blind A/B `cedar` vs `marin` **over an actual phone call**, not laptop speakers — 8 kHz μ-law band-limiting changes which voice sounds natural (`docs/PRD.md:776`)
- [ ] **[D1]** Measure real cost per minute across 10 calls; replace the `[uncertain]` $0.45/4-min target in the PRD with a measured number

### ✅ Gate 1 — the demo's spine

Place a real call. Interrupt the agent mid-sentence. Show the `barge_in` event and the measured cut-off latency in the logs.

- [ ] **[D0]** Voice-to-voice p50 ≤ 800 ms measured across ≥ 10 turns
- [ ] **[D0]** Voice-to-voice p95 ≤ 1,400 ms
- [ ] **[D0]** Barge-in cut-off ≤ 200 ms
- [ ] **[D0]** Truncation accuracy within ±100 ms
- [ ] **[D0]** Answer latency ≤ 6 s from ring

**If p95 misses by a lot, stop and fix it here.** Every later track adds work to the same hot path, and latency debt never gets cheaper to pay down.

---

## Track 2 — Tools & Booking (Week 2–3)

### Registry

- [ ] **[D0]** `tools/registry.py` — `ToolResult` and `ToolSpec` TypedDicts exactly as `docs/PRD.md:430-442`
- [x] **[D0]** `ToolResult.status` is `ok | not_found | unavailable | denied` — never an exception, never an HTTP code
- [x] **[D0]** `speak_hint` on every non-`ok` result. **This field is what stops the agent inventing a booking that did not happen** — it is not a nicety, it is the mechanism
- [x] **[D0]** `ToolRegistry` maps name → schema, handler, `timeout_ms`, `idempotency_key`, filler phrase, `on_failure`
- [x] **[D0]** Registry filtered per call by `tools_enabled` from client YAML
- [x] **[D0]** Test: every handler forced to raise still returns a valid envelope

### Latency masking

- [x] **[D0]** `dispatch_with_masking()` per `docs/PRD.md:407-421`
- [x] **[D0]** Filler fires **only** when `timeout_ms > 250`; fillers are fixed strings, never model-generated
- [x] **[D0]** Out-of-band `response.create` with `input: []` so the filler never enters conversation state
- [x] **[D1]** Verify a sub-250 ms tool plays **no** filler — masking a 40 ms lookup makes the agent sound slower
- [ ] **[D0]** Measured dead air ≤ 400 ms across 10 booking calls

### The six tools

| Tool | Timeout | On failure | Priority |
|---|---|---|---|
| `check_service_area` | 100 ms | escalate | **[D0]** |
| `check_availability` | 1,200 ms | degrade | **[D0]** |
| `book_appointment` | 2,000 ms | degrade | **[D0]** |
| `lookup_knowledge` | 600 ms | degrade | **[D1]** |
| `transfer_to_human` | 3,000 ms | escalate | **[D1]** |
| `send_payment_link` | 1,500 ms | degrade | **[P]** |

- [x] **[D0]** `tools/service_area.py` — pure lookup against `config.service_area.postcodes`; p99 < 100 ms in the local implementation
- [x] **[D0]** `tools/availability.py` — Cal.com slot search, resolved into the client's timezone
- [ ] **[D0]** **Verify Cal.com's slot hold/release primitive actually exists** in the current API version. The PRD chose Cal.com over Google Calendar specifically for this (`docs/PRD.md:84`) and flags it `[uncertain]`. If it does not exist, decide now between write-then-delete or dropping the hold — do not discover this in Week 4.
- [x] **[D0]** `tools/booking.py` — idempotent on `(call_id, slot_start)`
- [x] **[D0]** Test: two concurrent identical booking calls produce **exactly one** appointment
- [x] **[D1]** `tools/knowledge.py` — pgvector top-3 cosine, minimum score **0.35**
- [x] **[D1]** Below threshold returns `not_found` so the agent says it will check rather than hallucinating
- [ ] **[D1]** Verified on 5 deliberately out-of-scope questions
- [x] **[D1]** `apps/api/cli/kb_ingest.py` — markdown → chunks → embeddings → `kb_chunks` (also fixes the broken entry point)
- [ ] **[D1]** Northside HVAC KB written and loaded, ~40 documents
- [x] **[D1]** `tools/transfer.py` — warm transfer by Twilio REST redirect, agent context spoken to the human first
- [x] **[D1]** `tools/crm.py` — `CRMPort` interface + HubSpot adapter, dedupe on E.164
- [x] **[D1]** `contacts` upsert relies on the UNIQUE constraint; concurrent-insert path is implemented
- [x] **[P]** `tools/payment.py` — Stripe Payment Link created and handed to SMS; **never** reads digits

### Domain logic — pure functions, zero I/O

- [x] **[D0]** `domain/hours.py` — business hours, tz math, `emergency_dispatch` windows
- [x] **[D1]** `domain/qualification.py` — book vs escalate vs decline
- [x] **[D0]** Unit coverage for the implemented `domain/` branches — it is pure, there is no excuse, and it is where booking-logic bugs hide

### ✅ Gate 2 — the booking

Call in, describe an AC failure, get a real appointment on a real calendar.

---

## Track 3 — Resilience (Week 3)

Beat 5 and beat 6 of the demo. This is what separates the pitch from every competitor's happy-path demo.

### Escalation — deterministic, never a model judgment

- [x] **[D0]** `domain/escalation.py` — `should_escalate()` per `docs/PRD.md:462-472`
- [x] **[D0]** Safety keywords match **first and pre-empt everything**, including an in-flight booking
- [x] **[D0]** Keyword match is a deterministic string test on the streaming transcript, fired regardless of what the model was about to say
- [x] **[D0]** Caller requests human → escalate
- [x] **[D1]** 3 consecutive tool failures → escalate
- [x] **[D1]** 2 negative-sentiment turns → escalate
- [x] **[D0]** Unit tests cover all five triggers **including safety-keyword precedence over an in-flight booking**
- [ ] **[D1]** Live sentiment via out-of-band classifier (`conversation: "none"`, `output_modalities: ["text"]`) — the GA client primitive exists, but live classifier wiring is still open

### Failure paths

- [x] **[D0]** Per-`ToolSpec` timeout and retry policy execution
- [x] **[D0]** `degrade` path offers a callback and creates a CRM task — **never** claims success
- [x] **[D0]** Fault-injection test forces a booking tool to time out; asserts the agent never claims a booking that did not happen
- [ ] **[D1]** Retries with jitter for Cal.com, HubSpot, Twilio REST, Stripe, OpenAI, Anthropic
- [ ] **[P]** Reconnect handling for Twilio and OpenAI socket drops mid-call

### Telephony edges

- [x] **[D1]** `telephony/status_webhook.py` — `no-answer` / `busy` / `failed` → missed-call text-back within 30 s
- [x] **[D1]** `telephony/sms.py` — outbound SMS, STOP/opt-out writes `contacts.opted_out_at`
- [x] **[D1]** Suppression list checked **before** every outbound SMS
- [ ] **[D1]** Missed-call text-back verified end to end on a real phone

### Compliance

- [x] **[D0]** Recording consent captured before the stream connects
- [x] **[D0]** `calls.consent_captured = false` blocks recording storage
- [x] **[D0]** PAN-like digit sequences redacted from caller turns before persistence — `security/redaction.py` **already exists**, wire it into the transcript write path
- [x] **[D0]** Test with 5 synthetic card patterns
- [ ] **[D1]** Confirm no card digits ever reach the audio path, the transcript, or the post-call LLM call
- [ ] **[P]** Redaction applied to logs and traces, not just DB writes
- [ ] **[P]** Document recording-consent behavior by jurisdiction before any real client use

### ✅ Gate 3 — the degraded path

Kill Cal.com mid-call. The agent promises a callback and creates the CRM task. Then say "I smell gas" and watch it transfer immediately.

---

## Track 4 — Post-Call Intelligence (Week 4)

- [x] **[D1]** Celery app with Redis broker
- [x] **[D1]** `workers/recording.py` — consent-gated recording storage and remote cleanup path
- [x] **[D1]** `workers/analyze.py` — Claude over the transcript
- [x] **[D1]** Schema-constrained JSON for `call_analyses`: summary, intent, sentiment, `qa_score` 0–100, action items
- [ ] **[D1]** 100% parse rate on 20 real transcripts — no repair loop
- [x] **[D1]** `workers/crm_sync.py` — HubSpot contact + call/contact upsert, idempotent on replay
- [ ] **[D1]** Confirmation SMS idempotent on replay
- [x] **[D1]** Per-step retry with backoff
- [ ] **[P]** Dead-letter queue and a review workflow
- [ ] **[D1]** Post-call artifacts visible in the dashboard ≤ 90 s after hangup
- [ ] **[P]** Data retention policy for recordings, transcripts, traces
- [ ] **[P]** Postgres backup schedule + a restore actually tested

---

## Track 5 — Dashboard (Week 4–5)

Beat 7. This is what the client looks at while you talk, and it is what makes the invisible work visible.

- [x] **[D0]** Next.js 15 App Router scaffolded in `apps/web` — there is no `package.json` today
- [ ] **[D0]** Tailwind + shadcn/ui; token-compatible styling exists, but shadcn component integration is still open
- [x] **[D0]** Inter for text, JetBrains Mono for every number compared vertically
- [x] **[D0]** REST + SSE routers in `apps/api/routers/`
- [x] **[D0]** Call list: outcome, duration, cost, latency columns
- [x] **[D1]** Filter by outcome and date
- [x] **[D0]** Call detail: transcript with **inline tool chips**, **barge-in markers**, per-turn latency
- [x] **[D1]** Latency over 1,400 ms renders in `--degraded`
- [x] **[D1]** Recording playback
- [x] **[D1]** Escalation timeline from `call_events`, chronological
- [ ] **[D1]** Live view over SSE: waveform, streaming transcript, active tool indicator, < 500 ms lag — implementation exists; lag is not measured
- [ ] **[P]** Analytics: calls by hour, outcome distribution, recovered-revenue estimate, p50/p95 trend
- [ ] **[P]** Agent config editor writing `config/clients/*.yaml` — `dump_client_config()` **already exists** in `loader.py`
- [ ] **[P]** Config reload requires no media-plane redeploy — hot-reload is **already built**, just prove it
- [ ] **[D1]** Mobile call detail verified at 390 px — responsive layout exists; browser verification remains
- [ ] **[D0]** Desktop verified at 1280 px — **this is the width you will demo at**; browser verification remains
- [x] **[D0]** Dashboard auth — even a shared bearer token; `DASHBOARD_API_TOKEN` is already in settings
- [x] **[P]** Admin/viewer role split

---

## Track 6 — Evals (Week 5)

- [x] **[D1]** `apps/eval/scenarios/` — 40 YAML scenarios
- [x] **[D1]** 28 happy path
- [x] **[D1]** 12 adversarial: interruption, out-of-area, hostile caller, silence, wrong number, safety keyword, tool timeout, caller changes slot, payment question, caller reads card digits, no availability, human request
- [x] **[D1]** `apps/eval/runner.py` — scripted caller through the real domain/tool graph (**not** through the media plane)
- [x] **[D1]** Hard graders: booking exists, correct slot, no false success claim, correct escalation, no PCI capture
- [ ] **[D1]** Claude-as-judge rubric for tone and task completion
- [x] **[D1]** Baseline run committed to the repo
- [x] **[D1]** ≥ 85% task success — local baseline is 40/40
- [x] **[D0]** **Zero critical safety failures** — local baseline has zero critical failures; live-provider behavior remains unverified

---

## Track 7 — Observability (parallel, Week 3 onward)

- [x] **[D1]** Structured JSON logs carrying `call_id`, `client_id`, `twilio_call_sid`, `stream_sid`, trace ID — `observability/logging.py` **already does call-scoped context**, extended for redaction
- [ ] **[D1]** Sentry on media-plane exceptions and worker failures; verify with a forced fault
- [x] **[D0]** `/health` covering api, Postgres, Redis
- [ ] **[P]** Langfuse: realtime session + post-call Claude chain under one trace ID
- [ ] **[D1]** Metrics: p50/p95 voice-to-voice, barge-in cut-off, truncation accuracy, tool latency/failure/retry, cost per call
- [ ] **[P]** Alerts: media-plane exception, high p95, OpenAI session failure, tool failure spike, worker backlog, post-call SLA miss

---

## Demo Day Runbook

Write this as `docs/DEMO_RUNBOOK.md` once Gate 3 passes.

### 48 hours before

- [ ] **[D0]** Full dress rehearsal end to end, recorded, timed
- [ ] **[D0]** Rehearse **twice more** — once badly on purpose (interrupt at the worst moment, mumble, talk over the greeting)
- [ ] **[D0]** Confirm Cal.com has open slots on the demo date; a "no availability" answer mid-demo reads as broken
- [ ] **[D0]** Confirm Twilio balance and that the number still routes
- [ ] **[D0]** Seed 8–10 realistic historical calls so the dashboard is not an empty state
- [ ] **[D1]** Charge the demo phone; test the speakerphone in the actual room
- [ ] **[D1]** Export the architecture diagram
- [ ] **[D1]** Record a 3-minute backup demo video — **this is the fallback if the live call fails**

### 2 hours before

- [ ] **[D0]** `alembic upgrade head` clean
- [ ] **[D0]** All services up; `/health` green
- [ ] **[D0]** ngrok tunnel up on the reserved domain; Twilio webhook confirmed pointing at it
- [ ] **[D0]** One live smoke call, start to finish, verified in the dashboard
- [ ] **[D0]** Dashboard open at 1280 px, logged in, on the call list
- [ ] **[D1]** Terminal ready with the Cal.com kill command pre-typed for beat 5

### Failure plan

- [ ] **[D0]** If the live call fails: cut to the recorded video within 10 seconds. Have it open in a background tab already.
- [ ] **[D0]** If latency is visibly bad: name it before the client does, show the p95 chart, explain the budget. Owning it beats being caught.
- [ ] **[D1]** If a tool fails unscripted: that *is* beat 5. Lean in.

---

## Risk Register

| # | Risk | Impact | Mitigation | Owner |
|---|---|---|---|---|
| 1 | OpenAI Realtime GA event shapes differ from PRD | **Blocks everything** — most tutorials are still on beta shapes | Prove one session on day 1, before writing the bridge | — |
| 2 | Cal.com has no slot-hold primitive | Booking design changes | Verify in Week 2 (`docs/PRD.md:84` flags it) | — |
| 3 | `gpt-realtime-2.1` name/availability differs | Session init fails | Verify the model ID against your key on day 1 | — |
| 4 | p95 latency misses 1,400 ms | Demo sounds sluggish; the core claim weakens | Measure at Gate 1; do not proceed on a miss | — |
| 5 | ngrok URL rotates mid-demo | Dead phone number | Reserved domain, not a free ephemeral tunnel | — |
| 6 | Model claims a booking that failed | **Credibility-ending on a live call** | `speak_hint` envelope + fault-injection tests in Track 3 | — |
| 7 | Carrier jitter breaks mark accounting | Truncation drifts, conversation desyncs | The five ack ordering tests in Track 1 | — |
| 8 | 5-week plan, ~600 lines written in 11 days | Schedule slip | Cut to beats 3 and 5; **[P]** items are all deferrable | — |

---

## Explicitly out of the demo

Say these out loud during the demo as roadmap, not as gaps. Naming them first is the difference between "not built yet" and "not built yet, deliberately."

- Outbound lead reactivation (different TCPA consent regime — the first upsell, not the MVP)
- Multilingual agents
- DTMF / spoken card capture (deliberately out of PCI scope; payment links do the same job)
- Multi-location routing
- Supervisor barge/whisper/monitor
- SOC 2 / HIPAA posture (HVAC vertical chosen precisely so PHI never appears)
- Autoscaling / multi-region media
- Self-serve KB editing in the dashboard

---

## Progress

### Implementation Evidence

- Ordered commits: `bb41c03`, `38fa9e3`, `07e38db`, `87f802c`, `5093be2`, `94d83a1`, `b290a6d`, `28da055`.
- `uv run ruff check .` passed.
- `uv run mypy apps` passed across 61 files.
- `uv run pytest` passed with 110 tests.
- `uv run eval-run --json` passed 40/40 scenarios with zero critical failures.
- `npm run build` passed for `apps/web` on Next.js 15.5.22.
- `uv run alembic heads` reports `0002_initial_schema`; clean-DB upgrade still needs a running Postgres/pgvector service.
- Not yet verified: real Twilio/OpenAI/Cal.com/HubSpot calls, live latency gates, `docker compose up`, live `/health`, clean-DB migration execution, browser-width checks, and provider credentials.

| Track | Items | Implemented | Gate |
|---|---|---|---|
| 0 — Foundation | 28 | 18 | App boots, migrations run |
| 1 — Media plane | 47 | 43 | **Live call + barge-in** |
| 2 — Tools & booking | 27 | 23 | **Real appointment booked** |
| 3 — Resilience | 22 | 17 | **Degraded path + safety** |
| 4 — Post-call | 13 | 6 | Artifacts ≤ 90 s |
| 5 — Dashboard | 19 | 11 | Walkthrough at 1280 px |
| 6 — Evals | 10 | 8 | ≥ 85%, zero safety failures |
| 7 — Observability | 7 | 2 | Health + metrics |

**Demo-ready** = Tracks 0–3 complete at **[D0]**, plus Track 5 call list and call detail.
**Production-ready** = everything, including **[P]**.
