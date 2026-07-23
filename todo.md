# Revenue Recovery Voice Agent TODO

Production-ready checklist derived from `docs/PRD.md`.

## Definition of Done

- [ ] A real inbound Twilio call reaches the FastAPI media WebSocket and completes a two-way spoken conversation.
- [ ] Twilio audio and OpenAI Realtime audio stay in native `audio/pcmu` end to end with no hot-path transcoding.
- [ ] Barge-in handling sends Twilio `clear`, OpenAI `response.cancel`, and `conversation.item.truncate` in the required order.
- [ ] Tool calls never leak raw exceptions to the model; every tool returns the shared `ToolResult` envelope.
- [ ] Booking, CRM, SMS, payment-link, and post-call analysis flows are idempotent and retry-safe.
- [ ] Dashboard shows call history, live call state, transcript, timeline events, tool calls, latency, cost, and outcome.
- [ ] Eval suite has 40 scenarios and reaches at least 85% booking/task success.
- [ ] Production deployment has secrets, logs, metrics, traces, error alerts, backups, and rollback documented.

## Phase 0 - Project Foundation

- [ ] Decide whether `backend/` is obsolete and remove or migrate any remaining references.
- [ ] Finalize root project layout to match PRD: `apps/api`, `apps/web`, `apps/eval`, `config/clients`, `docs`.
- [ ] Add Python dependency manager setup for `apps/api` with Python 3.12.
- [ ] Add Next.js 15 app setup under `apps/web` with Tailwind and shadcn/ui.
- [ ] Add local `.env.example` documenting every required secret and URL.
- [ ] Add `README.md` with local setup, service overview, and one-command Docker Compose boot.
- [ ] Add lint, format, typecheck, and test scripts.
- [ ] Add CI workflow for backend tests, frontend checks, and Docker build validation.
- [ ] Expand `docker-compose.yml` for api, worker, web, postgres, redis, and optional Langfuse/Sentry-compatible config.

## Phase 1 - Media Plane

- [ ] Implement `/twiml/incoming` to resolve Twilio `To` number to client config.
- [ ] Return TwiML with recording-consent preamble and `<Connect><Stream>` media URL.
- [ ] Implement Twilio Media Streams WebSocket gateway at `/media/{call_id}`.
- [ ] Parse Twilio `connected`, `start`, `media`, `mark`, and `stop` events.
- [ ] Implement `RealtimeClient` for OpenAI Realtime GA WebSocket.
- [ ] Send GA-compatible `session.update` with `session.type`, `audio.input.format`, `audio.output.format`, stored prompt ID, variables, and tools.
- [ ] Forward Twilio base64 G.711 mu-law payloads as `input_audio_buffer.append` without decoding or resampling.
- [ ] Forward `response.output_audio.delta` back to Twilio as `media` events.
- [ ] Send a Twilio `mark` after every outbound audio chunk.
- [ ] Implement `PlaybackLedger` to convert acknowledged marks to played milliseconds.
- [ ] Persist ledger state in Redis so live state survives worker restarts where possible.
- [ ] Implement barge-in controller with exact ordering: `clear`, `response.cancel`, `conversation.item.truncate`.
- [ ] Record `truncated_at_ms` for interrupted agent turns.
- [ ] Instrument `speech_stopped` to first outbound Twilio media byte latency per turn.
- [ ] Persist `calls`, `turns`, and `call_events` rows.
- [ ] Unit test mark ack sequences: in-order, out-of-order, missing, duplicated, and late acks.
- [ ] Integration test Realtime session init against GA event shapes.
- [ ] Live demo gate: interrupt the agent mid-sentence and verify truncation event plus cut-off latency in logs.

## Phase 2 - Tools And Booking

- [ ] Define `ToolResult` and `ToolSpec` types in `apps/api/tools/registry.py`.
- [ ] Implement `ToolRegistry` with schema, handler, timeout, idempotency key, filler phrase, and failure policy per tool.
- [ ] Implement `check_service_area` against `config/clients/*.yaml` postcode list.
- [ ] Implement Cal.com availability search.
- [ ] Verify Cal.com slot hold/release support and update PRD uncertainty once confirmed.
- [ ] Implement `book_appointment` with idempotency on `(call_id, slot_start)`.
- [ ] Implement `lookup_knowledge` with pgvector top-k search and minimum cosine threshold 0.35.
- [ ] Add KB ingestion CLI: markdown to chunks to embeddings to `kb_chunks`.
- [ ] Load Northside HVAC demo knowledge base with about 40 documents.
- [ ] Implement HubSpot `CRMPort` adapter with dedupe by E.164.
- [ ] Implement Stripe Payment Link creation and SMS delivery handoff.
- [ ] Implement Twilio warm transfer redirect.
- [ ] Implement latency masking fillers for tools with timeout over 250 ms.
- [ ] Ensure tools below 250 ms do not play filler audio.
- [ ] Test all tool failures return `ok`, `not_found`, `unavailable`, or `denied`, never raw exceptions.
- [ ] Test concurrent duplicate bookings create exactly one appointment.
- [ ] Live demo gate: call in, describe AC failure, and book a real calendar appointment.

## Phase 3 - Resilience And Telephony Edge Cases

- [ ] Add timeout and retry policy execution per `ToolSpec`.
- [ ] Add degraded-path behavior so failed booking offers callback instead of claiming success.
- [ ] Implement deterministic escalation predicates in `apps/api/domain/escalation.py`.
- [ ] Escalate immediately on safety keywords: gas smell, carbon monoxide, smoke, sparking.
- [ ] Escalate on caller human request.
- [ ] Escalate or degrade after repeated tool failures per PRD policy.
- [ ] Add live sentiment classifier using out-of-band text response with no conversation-state pollution.
- [ ] Implement missed-call text-back for Twilio `no-answer`, `busy`, and `failed` callbacks.
- [ ] Implement STOP/opt-out handling and suppression list checks before outbound SMS.
- [ ] Implement budget guard with 80% soft wrap-up and 100% graceful hard cutoff.
- [ ] Capture recording consent before stream connects.
- [ ] Block recording storage when `calls.consent_captured` is false.
- [ ] Redact PAN-like card digit sequences from caller transcript before persistence.
- [ ] Add fault-injection tests for each tool timeout and failure policy.
- [ ] Live demo gate: force Cal.com failure mid-call and verify callback promise plus CRM task.

## Phase 4 - Data, Workers, And Post-Call Intelligence

- [ ] Add Alembic migrations for `calls`, `turns`, `tool_invocations`, `call_events`, `contacts`, `call_analyses`, and `kb_chunks`.
- [ ] Enable PostgreSQL `pgvector` extension in migrations.
- [ ] Add indexes for call listing, contact lookup, and vector search.
- [ ] Implement Celery app with Redis broker.
- [ ] Implement post-call chain: fetch recording, transcribe or retrieve transcript, Claude analysis, persist, CRM sync, SMS.
- [ ] Add retries with backoff and dead-letter handling per worker step.
- [ ] Implement Claude analyzer JSON schema for summary, intent, sentiment, QA score, and action items.
- [ ] Validate Claude analyzer parse rate on 20 real transcripts with 100% schema-valid output.
- [ ] Make HubSpot sync idempotent on replay.
- [ ] Make SMS confirmation idempotent on replay.
- [ ] Ensure post-call artifacts appear in dashboard within 90 seconds after hangup.
- [ ] Add data retention policy for recordings, transcripts, and traces.
- [ ] Add backup and restore procedure for Postgres.

## Phase 5 - Eval Harness

- [ ] Create 40 YAML scenarios under `apps/eval/scenarios`.
- [ ] Include 28 happy-path scenarios.
- [ ] Include 12 adversarial scenarios: interruptions, out-of-area, hostile caller, silence, wrong number, safety keyword, tool timeout, caller changes slot, caller asks payment question, caller reads card digits, no availability, and human request.
- [ ] Implement scripted caller runner through the real domain/tool graph.
- [ ] Add hard graders for booking exists, correct slot, no false success claim, correct escalation, and no PCI capture.
- [ ] Add Claude-as-judge rubric for tone and task completion.
- [ ] Record baseline eval run in the repo.
- [ ] Gate release on at least 85% task success.
- [ ] Gate release on zero critical safety failures.

## Phase 6 - Dashboard

- [ ] Implement dark operations-console theme using PRD tokens.
- [ ] Add call list with outcome, duration, cost, and latency columns.
- [ ] Add filters by outcome and date.
- [ ] Add call detail view with transcript, inline tool chips, barge-in markers, latency, and recording playback.
- [ ] Add chronological escalation and event timeline from `call_events`.
- [ ] Add live view over SSE with waveform, streaming transcript, active tool indicator, and under 500 ms lag.
- [ ] Add analytics: calls by hour, outcome distribution, recovered-revenue estimate, p50/p95 latency trend.
- [ ] Add agent config editor for `config/clients/*.yaml` with schema validation.
- [ ] Ensure config reload does not require media-plane redeploy.
- [ ] Verify mobile call-detail view at 390 px.
- [ ] Verify desktop layout at 1280 px and wider.

## Production Readiness

### Security And Compliance

- [ ] Store all secrets in environment variables or deployment secret store, never in repo.
- [ ] Validate Twilio webhook signatures for TwiML, status callbacks, and SMS webhooks.
- [ ] Authenticate dashboard routes.
- [ ] Add role model for at least admin and viewer if dashboard is exposed beyond local demo.
- [ ] Redact phone numbers in logs where full E.164 is not required.
- [ ] Redact card-like digit sequences before logs, traces, database writes, and post-call LLM calls.
- [ ] Add CORS policy scoped to deployed dashboard origin.
- [ ] Add rate limits for public webhook and API endpoints where compatible with Twilio traffic.
- [ ] Confirm payment flow never captures spoken card data.
- [ ] Document recording consent behavior by jurisdiction before real client use.

### Reliability

- [ ] Add health endpoints for api, worker, Redis, and Postgres dependencies.
- [ ] Add graceful shutdown for active media WebSocket sessions.
- [ ] Add reconnect/close handling for Twilio and OpenAI WebSocket failures.
- [ ] Add bounded queues/backpressure so slow outbound sends do not grow memory without limit.
- [ ] Add per-call max duration and max cost enforcement.
- [ ] Add retries with jitter for Cal.com, HubSpot, Twilio REST, Stripe, OpenAI, and Anthropic calls where safe.
- [ ] Add idempotency keys for booking, CRM writes, SMS sends, and post-call jobs.
- [ ] Add dead-letter queue review workflow.

### Observability

- [ ] Add structured JSON logs with `call_id`, `client_id`, `twilio_call_sid`, `stream_sid`, and trace ID.
- [ ] Add Sentry for media-plane exceptions and worker failures.
- [ ] Add Langfuse traces for realtime session and post-call Claude chain under one trace ID.
- [ ] Track p50/p95 voice-to-voice latency.
- [ ] Track barge-in cut-off latency.
- [ ] Track truncation accuracy.
- [ ] Track tool latency, tool failure rate, and retry count.
- [ ] Track cost per call and cost per minute.
- [ ] Alert on media-plane exception, high p95 latency, OpenAI session failures, tool failure spike, worker backlog, and post-call SLA miss.

### Deployment

- [ ] Build production Docker images for api, worker, and web.
- [ ] Pin Python, Node, and package versions.
- [ ] Add database migration command to deployment runbook.
- [ ] Add environment-specific configs for local, staging, and production.
- [ ] Add TLS termination for HTTPS and WSS.
- [ ] Configure Twilio webhook URLs for deployed TwiML, media stream, status callback, and SMS endpoints.
- [ ] Add rollback plan for app version and database migrations.
- [ ] Add Postgres backup schedule and restore test.
- [ ] Add Redis persistence policy decision for live ephemeral state.

### Performance Targets

- [ ] Answer latency: inbound ring to first word <= 6 seconds.
- [ ] Voice-to-voice latency: p50 <= 800 ms, p95 <= 1,400 ms.
- [ ] Barge-in cut-off <= 200 ms.
- [ ] Truncation accuracy within +/-100 ms.
- [ ] Tool-call dead air <= 400 ms.
- [ ] Tool success rate including retries >= 99%.
- [ ] Post-call artifacts available <= 90 seconds after hangup.
- [ ] Missed-call text-back queued <= 30 seconds after status callback.
- [ ] Cost per 4-minute call measured and kept <= $0.45 or PRD target updated with data.

## Launch Gates

- [ ] Phase 1 live call demo passes.
- [ ] Phase 2 real booking demo passes.
- [ ] Phase 3 degraded-path demo passes.
- [ ] Phase 4 eval suite passes with required score.
- [ ] Phase 5 dashboard walkthrough passes on desktop and mobile.
- [ ] No critical security/compliance blockers remain.
- [ ] No P0/P1 bugs remain open.
- [ ] README and deployment runbook are current.
- [ ] Demo video and architecture diagram are exported.
- [ ] Final production smoke test completed on the deployed phone number.
