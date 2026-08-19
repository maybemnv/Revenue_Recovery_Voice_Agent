import { expect, test } from "@playwright/test";

const apiUrl = "http://localhost:8101";
const webUrl = "http://localhost:3101";

test("fixture operator flow shows persisted call, degraded booking, escalation, live replay, and analytics", async ({ page, request }) => {
  const reset = await request.post(`${apiUrl}/api/demo/reset-and-replay`);
  await expect(reset).toBeOK();
  const replayState = await reset.json();
  expect(replayState).toMatchObject({ fixture: true, simulated: true, ready: true });

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`${webUrl}/calls`);
  await expect(page.getByText("Simulated fixture data")).toBeVisible();
  await expect(page.getByText("booked", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Review" }).click();
  await expect(page.getByText("Transcript")).toBeVisible();
  await expect(page.getByText("degraded", { exact: true })).toBeVisible();
  await expect(page.getByText("safety_keyword")).toBeVisible();
  await expect(page.getByText("confirm_appointment", { exact: true })).toBeVisible();
  await expect(page.getByText("update_crm", { exact: true })).toBeVisible();

  await page.goto(`${webUrl}/live`);
  await expect(page.getByText("SSE connected")).toBeVisible();
  const replay = await request.post(`${apiUrl}/api/demo/reset-and-replay`);
  await expect(replay).toBeOK();
  await expect(page.getByText("simulated fixture")).toBeVisible();

  const aggregateRequest = page.waitForRequest(request => request.url().includes("/api/backend/metrics?"));
  const latencyRequest = page.waitForRequest(request => request.url().includes("/api/backend/metrics/latency?"));
  await page.goto(`${webUrl}/analytics`);
  expect(new URL((await aggregateRequest).url()).searchParams.get("client_id")).toBe(replayState.client_id);
  expect(new URL((await latencyRequest).url()).searchParams.get("client_id")).toBe(replayState.client_id);
  await expect(page.getByText("Fixture analytics")).toBeVisible();
  await expect(page.getByLabel("Fixture analytics").getByText("Calls")).toBeVisible();
  await expect(page.getByLabel("Fixture analytics").getByText("1", { exact: true })).toHaveCount(2);
  await expect(page.getByLabel("Fixture analytics").getByText("$0.47", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Fixture analytics").getByText("420", { exact: true })).toBeVisible();

  await page.goto(`${webUrl}/agent`);
  await expect(page.getByRole("heading", { name: "Agent surface" })).toBeVisible();
  await expect(page.getByText("prompt: pmpt_northside_v4")).toBeVisible();
  await expect(page.getByText("service area")).toBeVisible();
});

test("analytics uses the server-configured fixture client and excludes unrelated rows", async ({ page }) => {
  await page.route("**/api/backend/**", route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/health/ready")) return route.fulfill({ json: {
      status: "ready",
      fixture: true,
      simulated: true,
      fixture_client_id: "fixture-east",
      checks: { api: { ok: true }, postgres: { ok: true }, redis: { ok: true }, fixture_data: { ok: true } },
    } });
    const scoped = url.searchParams.get("client_id") === "fixture-east";
    if (url.pathname.endsWith("/metrics/latency")) return route.fulfill({ json: {
      voice_to_voice: { count: scoped ? 2 : 99, p50_ms: scoped ? 420 : 999, p95_ms: 510, max_ms: 510 },
    } });
    if (url.pathname.endsWith("/metrics")) return route.fulfill({ json: scoped
      ? { total_calls: 1, booked: 1, escalated: 0, booking_rate: 1, cost_usd: 0.47, avg_duration_seconds: 192, p50_response_latency_ms: 465 }
      : { total_calls: 99, booked: 88, escalated: 77, booking_rate: 0.88, cost_usd: 999, avg_duration_seconds: 999, p50_response_latency_ms: 999 } });
    return route.abort();
  });

  await page.goto(`${webUrl}/analytics`);

  await expect(page.getByLabel("Fixture analytics").getByText("1", { exact: true })).toHaveCount(2);
  await expect(page.getByLabel("Fixture analytics").getByText("420", { exact: true })).toBeVisible();
  await expect(page.getByText("99", { exact: true })).toHaveCount(0);
  await expect(page.getByText("999", { exact: true })).toHaveCount(0);
});

test("fixture call ledger has no horizontal overflow at mobile width", async ({ page, request }) => {
  await request.post(`${apiUrl}/api/demo/reset-and-replay`);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${webUrl}/calls`);
  await expect(page.getByText("Simulated fixture data")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});
