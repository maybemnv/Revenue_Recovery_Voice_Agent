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

const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

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
