import { NextRequest } from "next/server";

const FORWARDED_RESPONSE_HEADERS = [
  "cache-control",
  "content-type",
  "retry-after",
  "x-accel-buffering",
] as const;

type RouteContext = { params: Promise<{ path: string[] }> };

function backendUrl(path: string[], request: NextRequest): string {
  const base = (process.env.API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
  const suffix = path.map(encodeURIComponent).join("/");
  // Health endpoints live at the API root (`/health/ready`), while dashboard
  // routes are registered under `/api`; the proxy prepends `/api/` only for
  // the latter so both trees stay reachable through the same catch-all.
  const prefix = path[0] === "health" ? "" : "/api/";
  return `${base}${prefix}${suffix}${new URL(request.url).search}`;
}

function backendHeaders(request: NextRequest): Headers {
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  const accept = request.headers.get("accept");
  if (contentType) headers.set("content-type", contentType);
  if (accept) headers.set("accept", accept);

  // The token is read only by this server-side route. It is never serialized
  // into the page, browser bundle, query string, or SSE URL.
  const token = process.env.DASHBOARD_VIEWER_TOKEN ?? process.env.DASHBOARD_API_TOKEN;
  if (token) headers.set("authorization", `Bearer ${token}`);
  return headers;
}

async function proxy(request: NextRequest, context: RouteContext): Promise<Response> {
  const { path } = await context.params;
  const body = request.method === "GET" || request.method === "HEAD"
    ? undefined
    : await request.arrayBuffer();

  try {
    const upstream = await fetch(backendUrl(path, request), {
      method: request.method,
      headers: backendHeaders(request),
      body,
      cache: "no-store",
    });

    const headers = new Headers();
    for (const name of FORWARDED_RESPONSE_HEADERS) {
      const value = upstream.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new Response(upstream.body, {
      status: upstream.status,
      headers,
    });
  } catch {
    return Response.json({ detail: "dashboard API is unavailable" }, { status: 502 });
  }
}

export const dynamic = "force-dynamic";

export const GET = proxy;
export const HEAD = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
