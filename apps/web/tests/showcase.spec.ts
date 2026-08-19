import { expect, test } from "@playwright/test";

const apiUrl = "http://localhost:8101";
const webUrl = "http://localhost:3101";

test("fixture operator flow shows persisted call, degraded booking, escalation, live replay, and analytics", async ({ page, request }) => {
  const reset = await request.post(`${apiUrl}/api/demo/reset-and-replay`);
  await expect(reset).toBeOK();
  expect(await reset.json()).toMatchObject({ fixture: true, simulated: true, ready: true });

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`${webUrl}/calls`);
  await expect(page.getByText("Simulated fixture data")).toBeVisible();
  await expect(page.getByText("booked", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Review" }).click();
  await expect(page.getByText("Transcript")).toBeVisible();
  await expect(page.getByText("degraded", { exact: true })).toBeVisible();
  await expect(page.getByText("safety_keyword")).toBeVisible();

  await page.goto(`${webUrl}/live`);
  await expect(page.getByText("SSE connected")).toBeVisible();
  const replay = await request.post(`${apiUrl}/api/demo/reset-and-replay`);
  await expect(replay).toBeOK();
  await expect(page.getByText("simulated fixture")).toBeVisible();

  await page.goto(`${webUrl}/analytics`);
  await expect(page.getByText("Fixture analytics")).toBeVisible();
  await expect(page.getByText("Booked")).toBeVisible();
});

test("fixture call ledger has no horizontal overflow at mobile width", async ({ page, request }) => {
  await request.post(`${apiUrl}/api/demo/reset-and-replay`);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${webUrl}/calls`);
  await expect(page.getByText("Simulated fixture data")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});
