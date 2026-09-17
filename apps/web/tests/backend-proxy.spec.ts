import { expect, test } from "@playwright/test";
import { NextRequest } from "next/server";
import { GET } from "../app/api/backend/[...path]/route";

async function forwardedHeaders(requestHeaders: HeadersInit, fixtureMode: string): Promise<Headers> {
  process.env.APP_ENV = "local-fixture";
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

test("the server proxy does not fall back to localhost outside fixture mode", async () => {
  const previousEnvironment = process.env.APP_ENV;
  const previousApiBase = process.env.API_BASE_URL;
  const previousPublicApiBase = process.env.NEXT_PUBLIC_API_BASE_URL;
  process.env.APP_ENV = "staging";
  delete process.env.API_BASE_URL;
  delete process.env.NEXT_PUBLIC_API_BASE_URL;

  try {
    const response = await GET(
      new NextRequest("https://dashboard.example/api/backend/health"),
      { params: Promise.resolve({ path: ["health"] }) },
    );
    expect(response.status).toBe(502);
  } finally {
    if (previousEnvironment === undefined) delete process.env.APP_ENV;
    else process.env.APP_ENV = previousEnvironment;
    if (previousApiBase === undefined) delete process.env.API_BASE_URL;
    else process.env.API_BASE_URL = previousApiBase;
    if (previousPublicApiBase === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL;
    else process.env.NEXT_PUBLIC_API_BASE_URL = previousPublicApiBase;
  }
});
