import { expect, test } from "@playwright/test";
import { NextRequest } from "next/server";
import { GET } from "../app/api/backend/[...path]/route";

async function forwardedHeaders(requestHeaders: HeadersInit, fixtureMode: string): Promise<Headers> {
  process.env.FIXTURE_MODE = fixtureMode;
  process.env.DASHBOARD_API_TOKEN = "privileged-token";
  process.env.DASHBOARD_VIEWER_TOKEN = "viewer-token";

  let headers: Headers | undefined;
  const originalFetch = global.fetch;
  global.fetch = async (_input, init) => {
    headers = new Headers(init?.headers);
    return new Response(null, { status: 204 });
  };

  try {
    await GET(
      new NextRequest("http://dashboard.test/api/backend/fixture/clients", { headers: requestHeaders }),
      { params: Promise.resolve({ path: ["fixture", "clients"] }) },
    );
  } finally {
    global.fetch = originalFetch;
  }

  return headers!;
}

test("a browser fixture-role header cannot select the privileged bearer token", async () => {
  const fixtureHeaders = await forwardedHeaders({ "x-fixture-role": "admin" }, "true");
  expect(fixtureHeaders.get("x-fixture-role")).toBe("admin");
  expect(fixtureHeaders.get("authorization")).toBeNull();

  const serverHeaders = await forwardedHeaders({}, "false");
  expect(serverHeaders.get("authorization")).toBe("Bearer viewer-token");
});
