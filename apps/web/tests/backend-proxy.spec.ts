import { expect, test } from "@playwright/test";
import { NextRequest } from "next/server";
import { GET } from "../app/api/backend/[...path]/route";

const savedEnvironment = { ...process.env };
test.afterEach(() => {
  process.env = { ...savedEnvironment };
});

for (const host of ["[::1]", "[0:0:0:0:0:0:0:1]", "[::]", "0.0.0.0", "127.4.3.2", "127.1", "[::ffff:127.0.0.1]", "[::ffff:0.0.0.0]", "LOCALHOST.", "api.localhost"]) {
  test(`production proxy rejects ${host} before forwarding a token`, async () => {
    process.env.APP_ENV = "production";
    process.env.API_BASE_URL = `http://${host}:8000`;
    process.env.DASHBOARD_VIEWER_TOKEN = "test-private-token";
    let calls = 0;
    const originalFetch = global.fetch;
    global.fetch = async () => {
      calls++;
      return new Response(null, { status: 204 });
    };
    try {
      const response = await GET(
        new NextRequest("https://dashboard.example/api/backend/metrics"),
        { params: Promise.resolve({ path: ["metrics"] }) },
      );
      expect(response.status).toBe(502);
      expect(calls).toBe(0);
    } finally {
      global.fetch = originalFetch;
    }
  });
}

test("fixture proxy preserves the slash for root health routes", async () => {
  process.env.APP_ENV = "local-fixture";
  process.env.FIXTURE_MODE = "true";
  process.env.API_BASE_URL = "http://api-fixture:8101";

  const originalFetch = global.fetch;
  let target: unknown;
  global.fetch = async (input) => {
    target = input;
    return Response.json({ status: "ready", fixture: true, fixture_client_id: "northside-hvac" });
  };
  try {
    const response = await GET(
      new NextRequest("http://dashboard.test/api/backend/health/ready"),
      { params: Promise.resolve({ path: ["health", "ready"] }) },
    );
    expect(response.status).toBe(200);
    expect(target).toBe("http://api-fixture:8101/health/ready");
  } finally {
    global.fetch = originalFetch;
  }
});

test("production proxy permits private network backends with viewer authorization", async () => {
  process.env.APP_ENV = "production";
  process.env.API_BASE_URL = "http://10.0.0.2:8000";
  process.env.DASHBOARD_VIEWER_TOKEN = "test-viewer";
  const originalFetch = global.fetch;
  let target: unknown;
  let headers: Headers | undefined;
  global.fetch = async (input, init) => {
    target = input;
    headers = new Headers(init?.headers);
    return new Response(null, { status: 204 });
  };
  try {
    const response = await GET(
      new NextRequest("https://dashboard.example/api/backend/metrics?days=3"),
      { params: Promise.resolve({ path: ["metrics"] }) },
    );
    expect(response.status).toBe(204);
    expect(target).toBe("http://10.0.0.2:8000/api/metrics?days=3");
    expect(headers?.get("authorization")).toBe("Bearer test-viewer");
  } finally {
    global.fetch = originalFetch;
  }
});

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
