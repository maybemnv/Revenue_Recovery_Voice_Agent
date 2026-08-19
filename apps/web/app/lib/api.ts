export type CallSummary = {
  id: string;
  client_id: string;
  from_e164: string;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  outcome: string | null;
  cost_cents: number;
  has_recording: boolean;
  fixture: boolean;
  simulated: boolean;
};

export type CallDetail = CallSummary & {
  recording_url: string | null;
  turns: Array<{
    role: "caller" | "agent";
    text: string;
    at_ms: number;
    latency_ms: number | null;
    truncated_at_ms: number | null;
  }>;
  events: Array<{ at_ms: number; kind: string; payload: Record<string, unknown> }>;
  tool_invocations: Array<{
    name: string;
    status: string;
    latency_ms: number;
    attempt: number;
    arguments: Record<string, unknown>;
  }>;
};

export type DashboardMetrics = {
  total_calls: number;
  booked: number;
  escalated: number;
  booking_rate: number;
  cost_usd: number;
  avg_duration_seconds: number | null;
  p50_response_latency_ms: number | null;
};

export type FixtureReadiness = {
  fixture: boolean;
  simulated: boolean;
  fixture_client_id?: string;
};

export type LatencyMetrics = {
  voice_to_voice: {
    count: number;
    p50_ms: number | null;
    p95_ms: number | null;
    max_ms: number | null;
  };
};

// Calls stay same-origin so the Next.js server proxy can attach the viewer
// token without exposing it to browser JavaScript or putting it in a URL.
const base = "/api/backend";

export async function api<T>(path: string): Promise<T> {
  const response = await fetch(`${base}${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`API returned ${response.status}`);
  return response.json() as Promise<T>;
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "--";
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

export function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(value));
}
