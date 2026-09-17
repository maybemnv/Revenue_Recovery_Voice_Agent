# Plan 001: Make the Voice demo fixture-backed and showcase-ready

> **Executor instructions**: Follow this plan in order. Use red-green-refactor for every production behavior: add one focused test, run it and confirm it fails for the missing behavior, make the smallest implementation, then rerun the same test before proceeding. Run every final verification gate. If a STOP condition occurs, stop and report; do not improvise. After code review, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat e9f92fb..HEAD -- apps/api/settings.py apps/api/main.py apps/api/routers apps/api/observability apps/api/demo apps/web/app apps/web/package.json apps/web/package-lock.json apps/web/playwright.config.ts apps/web/tests Dockerfile docker-compose.yml .env.example README.md docs/DEMO_SCRIPT.md docs/deployment.md tests plans`
>
> Work in an isolated worktree if available. For subagent execution, dispatch one implementer per numbered step, then independently review that step's diff and test evidence before dispatching the next implementer. Do not run two implementers concurrently in this repository.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: MED
- **Depends on**: none
- **Category**: bug, tests, dx, docs, direction
- **Planned at**: commit `e9f92fb`, 2026-08-19

## Why this matters

The showcase requires an operator to reset the app, demonstrate an HVAC call, inspect its dashboard replay and analytics, and do so without paid provider credentials. Today the provider-free evaluator only returns an in-memory trace; it never creates the persisted calls that the dashboard reads. This plan creates a clearly labelled demo-only replay path, corrects the client analytics bug, then proves and documents the browser experience on the fixed showcase ports: API `8101`, web `3101`.

## Current state

- `apps/eval/runner.py` is a pure, credential-free evaluator. At lines 1-5 it says it does not open Twilio or OpenAI sockets; at lines 68-114 it returns `Trace` values only.
- `apps/api/settings.py:19-68` defines provider and dashboard settings but no fixture/demo mode or showcase-port contract.
- `apps/api/routers/dashboard.py:73-171` reads calls, turns, events, tool invocations, and analysis from PostgreSQL. It is therefore the correct existing read model for replay data.
- `apps/api/routers/dashboard.py:213-250` filters aggregate call counts by `client_id`, but the p50 query at lines 233-239 filters only by time. `latency_metrics` at lines 265-321 shows the correct scoped-query pattern.
- `apps/web/app/calls/page.tsx:22-24` fetches `/api/calls`; `apps/web/app/calls/[id]/page.tsx:13-18` fetches a persisted detail; `apps/web/app/live/page.tsx:12-20` consumes same-origin SSE through the server proxy.
- `apps/web/app/analytics/page.tsx:1-2` is a static empty state even though metrics endpoints exist.
- `apps/web/app/api/backend/[...path]/route.ts:18-56` is the established server-side proxy pattern. It reads the viewer token server-side and forwards upstream errors as safe JSON.
- `apps/api/observability/live.py:43-58` provides an in-process, bounded event hub. The existing documentation deliberately limits it to one API process, which is acceptable for this demo.
- `docker-compose.yml` exposes API `8000` and web `3000`; it starts provider-oriented services from `.env` and contains no fixture reset/replay path. `apps/web/Dockerfile:1-10` uses `npm install` despite `apps/web/package-lock.json` being committed.
- `docs/DEMO_SCRIPT.md:7-20` requires a real inbound provider call, while the portfolio showcase specification requires a deterministic call replay with no paid credentials.

Verified excerpts:

```python
# apps/eval/runner.py:1-5
"""Offline scenario runner for the domain and tool graph.

The evaluator intentionally does not open Twilio or OpenAI sockets.
"""
```

```tsx
// apps/web/app/analytics/page.tsx:1-2
export default function AnalyticsPage() {
  return <div className="page">...No call data is available...</div>;
}
```

```python
# apps/api/routers/dashboard.py:233-239
p50_latency = await session.scalar(
    select(func.percentile_cont(0.5).within_group(Turn.latency_ms))
    .select_from(Turn).join(Call, Call.id == Turn.call_id)
    .where(Turn.latency_ms.isnot(None), Call.started_at >= since)
)
```

Use the existing conventions: FastAPI routers live in `apps/api/routers`, persistence helpers in `apps/api/db/repository.py`, tests are pytest files under `tests/unit`, and the dashboard is a Next.js app using its same-origin proxy. Preserve the product's safety vocabulary: `fixture`/`simulated`, `booked`, `escalated`, and truthful degraded behavior. Never make a fixture outcome look like a live provider confirmation.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Python dependencies | `$env:UV_CACHE_DIR=(Join-Path (Get-Location) '.uv-cache'); uv sync --frozen` | exit 0 |
| Focused Python test | `$env:UV_CACHE_DIR=(Join-Path (Get-Location) '.uv-cache'); uv run pytest tests/unit/test_demo_replay.py -q` | focused tests pass |
| Full Python suite | `$env:UV_CACHE_DIR=(Join-Path (Get-Location) '.uv-cache'); uv run pytest -q` | all tests pass |
| Static checks | `$env:UV_CACHE_DIR=(Join-Path (Get-Location) '.uv-cache'); uv run ruff check apps tests; uv run mypy apps` | exit 0 |
| Offline evaluator | `$env:UV_CACHE_DIR=(Join-Path (Get-Location) '.uv-cache'); uv run eval-run --json` | score >= 0.85 and no critical failures |
| Web install/build | `npm --prefix apps/web ci; npm --prefix apps/web run build` | exit 0 |
| Fixture stack | `docker compose --profile fixture up --build -d` | API on 8101 and web on 3101 become healthy |
| Fixture reset/replay | `Invoke-RestMethod -Method Post http://localhost:8101/api/demo/reset-and-replay` | response labels data as fixture and reports ready |
| Browser smoke | `npm --prefix apps/web run test:showcase` | desktop and mobile fixture tests pass |

## Suggested executor toolkit

- Use `superpowers:test-driven-development` before each behavior change; retain the red command output in the implementation report.
- Use `superpowers:subagent-driven-development` if a controller delegates steps: one fresh implementer, then one independent task review per step.
- Use `superpowers:verification-before-completion` before any commit, push, PR, or completion claim.
- Read `docs/superpowers/specs/2026-08-19-six-demo-showcase-design.md` from the workspace root before Step 1. Its ten showcase gates are binding for this plan.

## Scope

**In scope** (only these files may be modified or created):

- `apps/api/settings.py`, `apps/api/main.py`, `apps/api/routers/health.py`, `apps/api/routers/dashboard.py`, `apps/api/routers/demo.py` (new), `apps/api/demo/__init__.py` (new), `apps/api/demo/replay.py` (new), `apps/api/observability/live.py`
- `tests/unit/test_demo_replay.py` (new), `tests/unit/test_dashboard.py` (new or extend only if it already exists)
- `apps/web/app/lib/api.ts`, `apps/web/app/calls/page.tsx`, `apps/web/app/calls/[id]/page.tsx`, `apps/web/app/live/page.tsx`, `apps/web/app/analytics/page.tsx`, `apps/web/package.json`, `apps/web/package-lock.json`, `apps/web/playwright.config.ts` (new), `apps/web/tests/showcase.spec.ts` (new), `apps/web/Dockerfile`
- `docker-compose.yml`, `.env.example`, `README.md`, `docs/DEMO_SCRIPT.md`, `docs/deployment.md`, `plans/README.md`

**Out of scope**:

- Real Twilio, OpenAI, Cal.com, HubSpot, Anthropic, Stripe, or SMS calls; no provider account, credential, webhook, or live-call rehearsal change.
- Production HA, multi-replica SSE, authentication redesign, recording policy, data retention, migration rewrites, and any change to the `config/clients` business configuration.
- Changing dashboard response schemas except to add a demo-only endpoint/field required by this plan; preserve current dashboard REST and SSE paths.
- Any root-workspace portfolio file, unrelated untracked file, or Git metadata.

## Git workflow

- Branch: `feat/demo-showcase-ready`.
- Commit checkpoints: (1) `test(demo): characterize deterministic call replay`; (2) `feat(demo): add fixture call replay`; (3) `fix(dashboard): scope latency metrics and render fixture analytics`; (4) `test(web): cover fixture showcase flow`; (5) `docs(demo): document fixture startup and reset`.
- Match the repository's conventional-commit style, e.g. `feat(media): ...` and `fix(retries): ...`.
- Do not push or open a pull request without explicit operator authorization after every final gate is green. If authorized, push only `feat/demo-showcase-ready`, open a focused PR, and include the exact command output plus a statement that live providers remain unverified.

## Steps

### Step 1: Specify the deterministic replay contract before implementation

Create `tests/unit/test_demo_replay.py` first. Write independent failing tests that use the existing session/repository seam and event hub to require:

1. a reset-and-replay operation creates a Northside HVAC fixture call with redacted caller display data, a transcript, tool invocations for service area and booking, a failed/degraded scheduling branch, a safety escalation, analysis, and a non-zero deterministic cost;
2. rerunning reset-and-replay yields the same named fixture records/metrics and does not duplicate rows;
3. every returned/public event and endpoint response includes a `fixture`/`simulated` label;
4. no provider client is constructed or network request is attempted by replay; and
5. `/health/ready` distinguishes process readiness from fixture-data readiness.

Run the focused file and record the expected failure because the demo module/router and fixture mode do not exist. Do not add production code before this red result.

**Verify (RED)**: `$env:UV_CACHE_DIR=(Join-Path (Get-Location) '.uv-cache'); uv run pytest tests/unit/test_demo_replay.py -q` → fails only for the missing replay/readiness behavior.

### Step 2: Implement the demo-only persisted replay and reset boundary

Create `apps/api/demo/replay.py` as the sole deterministic fixture definition. It must use existing repository helpers/models to write the same data shape consumed by `dashboard.py`, use a fixed clock/IDs and only the existing fictitious Northside client, and publish the finite replay event list through `get_hub()`. A reset must target only fixture-labelled/demo-client rows and be idempotent; it must not truncate arbitrary customer data.

Create `apps/api/routers/demo.py` with a single explicit demo-only `POST /api/demo/reset-and-replay` route. Gate it behind an explicit settings flag that defaults to enabled only in fixture mode; return a response that identifies it as simulated. Wire the router in `apps/api/main.py`. Extend `apps/api/settings.py` and `.env.example` with non-secret fixture-mode, fixed-port, and fixture-client configuration names. Extend `apps/api/routers/health.py` so readiness reports fixture-data readiness without returning caller content.

Do not route a fixture replay through the media bridge, tool factory, or a provider client. Use a small service object/dependency boundary so pytest can prove that fact. Keep live behavior untouched when fixture mode is off.

**Verify (GREEN)**: rerun `tests/unit/test_demo_replay.py -q` → all new tests pass; then run `$env:UV_CACHE_DIR=(Join-Path (Get-Location) '.uv-cache'); uv run pytest tests/unit/test_media_lifecycle.py tests/unit/test_tools.py -q` → existing media/tool behavior remains green.

### Step 3: Correct client-filtered metrics before changing the dashboard

Write a failing test in `tests/unit/test_dashboard.py` with two clients and distinct turn latencies. It must call `metrics(..., client_id=...)` and prove the returned p50 changes with the selected client. Confirm it fails because `dashboard.py:233-239` omits the predicate.

Implement the smallest correction by applying the same `Call.client_id == client_id` filter to the p50 query. Do not alter `latency_metrics` or its existing metric arithmetic unless its test exposes another real defect.

**Verify (RED then GREEN)**: `$env:UV_CACHE_DIR=(Join-Path (Get-Location) '.uv-cache'); uv run pytest tests/unit/test_dashboard.py -q` → first fails on cross-client p50, then passes after the minimal query change.

### Step 4: Render fixture data and meaningful analytics in the existing dashboard

Before implementation, add browser tests in `apps/web/tests/showcase.spec.ts` for the fixture primary story at `3101`: invoke the documented reset endpoint, open call ledger, inspect detail transcript/tool/event trail, observe the simulated live feed, and open analytics. The tests must assert visible `fixture`/`simulated` language, a booked fixture outcome, a degraded booking outcome that is not displayed as booked, and a safety escalation. Add a 1280px primary-flow case and a 390px no-horizontal-overflow case. Confirm the test fails because no browser runner/fixture behavior exists.

Add a direct test runner and script to `apps/web/package.json`, update `package-lock.json` through the package manager, and create `playwright.config.ts` to start the API fixture stack/web app against ports `8101` and `3101`. Do not use a real provider URL or credential. Update `apps/web/app/lib/api.ts`, calls/detail/live pages only as needed to render server-provided fixture state safely. Replace the static `analytics/page.tsx` placeholder with a read-only view of `/metrics` and `/metrics/latency`, retaining truthful empty/error states. Do not invent metrics when no fixture records exist.

**Verify (RED then GREEN)**: `npm --prefix apps/web run test:showcase` → first fails for missing script/fixture UI, then passes with both viewport cases.

### Step 5: Make the fixture deployment path reproducible and document it

Write/adjust the smallest configuration-level tests or smoke assertions necessary to fail on missing explicit ports and missing fixture readiness. Then update `docker-compose.yml` to provide a fixture profile that starts all required local services on API `8101` and web `3101`, without exposing a dependency on paid providers. Preserve any live-oriented path separately and keep local database/Redis restrictions clear. Change `apps/web/Dockerfile` from lockfile-ignoring installation to `npm ci` with the lockfile copied before install.

Update `README.md`, `docs/DEMO_SCRIPT.md`, and `docs/deployment.md` with one copy-pasteable fixture start sequence, health/readiness checks, reset/replay command, expected browser result, shutdown command, fixture/live boundary, and the fixed ports. Never include credentials or suggest that fixture output is live verification.

**Verify**: `docker compose --profile fixture up --build -d`; `Invoke-RestMethod http://localhost:8101/health`; `Invoke-RestMethod http://localhost:8101/health/ready`; `Invoke-RestMethod -Method Post http://localhost:8101/api/demo/reset-and-replay`; `Invoke-WebRequest http://localhost:3101` → all commands succeed and readiness identifies fixture data as ready. Then `docker compose --profile fixture down` → exits 0.

### Step 6: Run the complete gate and prepare the reviewable handoff

Run the complete verification sequence below in a clean dependency environment. Inspect `git status --short` and confirm every changed path is within Scope. Update `plans/README.md` only after independent review verifies each plan requirement. Commit at the checkpoints above; do not push/open a PR until explicitly authorized.

**Verify**: run every command in **Done criteria** and retain exact output in the PR body when authorized.

## Test plan

- `tests/unit/test_demo_replay.py`: deterministic reset, idempotent replay, persisted dashboard shape, simulation label, no provider construction, readiness behavior.
- `tests/unit/test_dashboard.py`: selected-client p50 never incorporates another client's latency.
- `apps/web/tests/showcase.spec.ts`: desktop primary flow, mobile layout, simulated booking/degradation/escalation, ledger/detail/live/analytics surfaces.
- Model persistence tests after the existing repository style in `tests/unit/test_metrics.py`; model API route assertions after `tests/unit/test_stream.py`.

## Done criteria

- [ ] Fixture mode needs no paid provider credential and has a safe, idempotent reset-and-replay command.
- [ ] Fixture API binds `8101`; fixture web binds `3101`; ports are documented and used by browser tests.
- [ ] `/health` and `/health/ready` distinguish running from fixture-data-ready without returning transcript or credential content.
- [ ] Dashboard ledger, detail, live feed, and analytics consume persisted simulated data and visibly label it as fixture/simulated.
- [ ] Client-filtered p50 has a regression test and passes.
- [ ] `$env:UV_CACHE_DIR=(Join-Path (Get-Location) '.uv-cache'); uv run pytest -q` passes.
- [ ] `$env:UV_CACHE_DIR=(Join-Path (Get-Location) '.uv-cache'); uv run ruff check apps tests` and `uv run mypy apps` pass.
- [ ] `$env:UV_CACHE_DIR=(Join-Path (Get-Location) '.uv-cache'); uv run eval-run --json` exits 0.
- [ ] `npm --prefix apps/web ci`, `npm --prefix apps/web run build`, and `npm --prefix apps/web run test:showcase` pass.
- [ ] Fixture Compose start, health, readiness, reset/replay, web request, and shutdown commands pass.
- [ ] `git status --short` contains no out-of-scope path; `plans/README.md` is updated after review.

## STOP conditions

- Any current-state excerpt materially differs after the drift check.
- The database schema cannot safely identify/delete only fixture rows; do not implement a broad reset.
- A replay implementation would import a provider adapter, require a credential, or trigger a provider network call.
- Binding 8101/3101 conflicts with a currently required service and no approved port-map adjustment is available.
- A browser test can pass only by consuming hard-coded frontend call/analytics data instead of the fixture API.
- Any test fails twice after a reasonable, scoped correction; report the failure/output rather than widening scope.
- A requested change requires out-of-scope migration, real-provider, security/auth, or portfolio-root work.

## Maintenance notes

- Keep all simulated data in `apps/api/demo/replay.py`; do not scatter demo constants through routers or React components.
- Reviewers must verify the reset targets only fixture records, response labels are present, and a degraded fixture can never be rendered as successful booking.
- Any future client/metrics filter must be applied to every aggregate query, following the regression test added here.
- Live-provider and recording claims remain unverified after this plan; the demo script must continue to say so.
