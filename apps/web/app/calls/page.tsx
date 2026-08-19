"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, CallSummary, formatDuration, formatTime } from "../lib/api";

type CallResponse = { items: CallSummary[]; total: number; fixture: boolean; simulated: boolean };

export default function CallsPage() {
  const [calls, setCalls] = useState<CallSummary[]>([]);
  const [outcome, setOutcome] = useState("");
  const [startedAfter, setStartedAfter] = useState("");
  const [startedBefore, setStartedBefore] = useState("");
  const [error, setError] = useState("");
  const [fixture, setFixture] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams();
    if (outcome) params.set("outcome", outcome);
    if (startedAfter) params.set("started_after", startedAfter);
    if (startedBefore) params.set("started_before", startedBefore);
    const query = params.toString() ? `?${params.toString()}` : "";
    api<CallResponse>(`/api/calls${query}`).then((data) => { setCalls(data.items); setFixture(data.fixture); }).catch(() => {
      setError("The API is not reachable. Start the API service to load call history.");
    });
  }, [outcome, startedAfter, startedBefore]);

  const booked = calls.filter((call) => call.outcome === "booked").length;
  const escalated = calls.filter((call) => call.outcome === "escalated").length;
  const cost = calls.reduce((sum, call) => sum + call.cost_cents, 0) / 100;

  return (
    <div className="page">
      <div className="page-heading">
        <div className="heading-copy">
          <div className="eyebrow">Operations / calls</div>
          <h1>Call ledger</h1>
          <p className="subhead">Every conversation, tool decision, and handoff in one reviewable surface.</p>
        </div>
        <div className="heading-meta"><Link className="button" href="/live">Open live monitor ↗</Link></div>
      </div>
      {fixture && <div className="tool-chip">Simulated fixture data — replay only, not a live provider confirmation.</div>}
      <div className="metric-strip" aria-label="Call metrics">
        <div className="metric"><div className="metric-label">Loaded calls</div><div className="metric-value">{calls.length}</div><div className="metric-note">Current result window</div></div>
        <div className="metric"><div className="metric-label">Booked</div><div className="metric-value">{booked}</div><div className="metric-note">{fixture ? "Fixture replay outcome" : "Provider-confirmed only"}</div></div>
        <div className="metric"><div className="metric-label">Escalated</div><div className="metric-value">{escalated}</div><div className="metric-note">Human attention required</div></div>
        <div className="metric"><div className="metric-label">Cost</div><div className="metric-value">${cost.toFixed(2)}</div><div className="metric-note">Loaded result window</div></div>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <section className="workbench" aria-labelledby="call-table-title">
        <div className="toolbar">
          <h2 id="call-table-title">Recent calls</h2>
          <div className="filters">
            <label className="sr-only" htmlFor="outcome">Filter by outcome</label>
            <select id="outcome" value={outcome} onChange={(event) => setOutcome(event.target.value)}>
              <option value="">All outcomes</option>
              <option value="booked">Booked</option>
              <option value="qualified">Qualified</option>
              <option value="escalated">Escalated</option>
              <option value="failed">Failed</option>
            </select>
            <label className="sr-only" htmlFor="started-after">From date</label>
            <input id="started-after" type="date" value={startedAfter} onChange={(event) => setStartedAfter(event.target.value)} />
            <label className="sr-only" htmlFor="started-before">To date</label>
            <input id="started-before" type="date" value={startedBefore} onChange={(event) => setStartedBefore(event.target.value)} />
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Caller</th><th>Started</th><th>Duration</th><th>Outcome</th><th>Cost</th><th /></tr></thead>
            <tbody>
              {calls.map((call) => (
                <tr key={call.id}>
                  <td data-label="Caller"><div className="cell-title">{call.from_e164}</div><div className="cell-meta">{call.client_id}</div></td>
                  <td className="mono" data-label="Started">{formatTime(call.started_at)}</td>
                  <td className="mono" data-label="Duration">{formatDuration(call.duration_seconds)}</td>
                  <td data-label="Outcome"><span className={`status ${call.outcome ?? "failed"}`}>{call.outcome ?? "in progress"}</span>{call.fixture && <div className="cell-meta">simulated</div>}</td>
                  <td className="mono" data-label="Cost">${(call.cost_cents / 100).toFixed(2)}</td>
                  <td data-label=""><Link className="button quiet" href={`/calls/${call.id}`}>Review</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
          {calls.length === 0 && <div className="empty">No calls match this view. New calls will appear here after the media plane writes them.</div>}
        </div>
      </section>
    </div>
  );
}
