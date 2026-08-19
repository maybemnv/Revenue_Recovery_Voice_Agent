"use client";

import { useEffect, useState } from "react";
import { api, DashboardMetrics, FixtureReadiness, formatDuration, LatencyMetrics } from "../lib/api";

export default function AnalyticsPage() {
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [latency, setLatency] = useState<LatencyMetrics | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<FixtureReadiness>("/health/ready")
      .then((readiness) => {
        if (!readiness.fixture_client_id) throw new Error("Fixture client is not configured");
        const query = `?client_id=${encodeURIComponent(readiness.fixture_client_id)}`;
        return Promise.all([
          api<DashboardMetrics>(`/api/metrics${query}`),
          api<LatencyMetrics>(`/api/metrics/latency${query}`),
        ]);
      })
      .then(([aggregate, latencyReport]) => {
        setMetrics(aggregate);
        setLatency(latencyReport);
      })
      .catch(() => setError("The API is not reachable. Start the API service to load analytics."));
  }, []);

  return <div className="page"><div className="page-heading"><div className="heading-copy"><div className="eyebrow">Operations / analytics</div><h1>Fixture analytics</h1><p className="subhead">Read-only metrics from persisted simulated calls; no provider outcome is claimed here.</p></div></div>{error && <div className="error-banner" role="alert">{error}</div>}{metrics && latency ? <section className="metric-strip" aria-label="Fixture analytics"><div className="metric"><div className="metric-label">Calls</div><div className="metric-value">{metrics.total_calls}</div></div><div className="metric"><div className="metric-label">Booked</div><div className="metric-value">{metrics.booked}</div></div><div className="metric"><div className="metric-label">Escalated</div><div className="metric-value">{metrics.escalated}</div></div><div className="metric"><div className="metric-label">Cost</div><div className="metric-value">${metrics.cost_usd.toFixed(2)}</div></div><div className="metric"><div className="metric-label">P50 response</div><div className="metric-value">{latency.voice_to_voice.p50_ms ?? "--"}</div><div className="metric-note">ms</div></div><div className="metric"><div className="metric-label">Average duration</div><div className="metric-value">{formatDuration(metrics.avg_duration_seconds)}</div></div></section> : !error && <section className="panel"><div className="placeholder">No call data is available for this window. This is intentional: the dashboard never invents conversion or revenue metrics.</div></section>}</div>;
}
