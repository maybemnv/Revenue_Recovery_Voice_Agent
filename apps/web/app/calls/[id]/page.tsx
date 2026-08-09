"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, CallDetail, formatDuration } from "../../lib/api";

export default function CallDetailPage() {
  const params = useParams<{ id: string }>();
  const [call, setCall] = useState<CallDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!params.id) return;
    api<CallDetail>(`/api/calls/${params.id}`).then(setCall).catch(() => {
      setError("This call could not be loaded from the API.");
    });
  }, [params.id]);

  if (error) return <div className="page"><div className="error-banner" role="alert">{error}</div><Link className="button quiet" href="/calls">Back to calls</Link></div>;
  if (!call) return <div className="page"><div className="eyebrow">Call detail</div><h1>Loading call...</h1></div>;

  return (
    <div className="page">
      <div className="page-heading">
        <div><Link className="eyebrow" href="/calls">Back to call ledger</Link><h1>{call.from_e164}</h1><p className="subhead">{call.client_id} / {call.outcome ?? "in progress"} / {formatDuration(call.duration_seconds)}</p></div>
        {call.recording_url && <audio controls src={`/api/backend/calls/${call.id}/recording`}>Recording playback</audio>}
      </div>
      <div className="detail-grid">
        <section className="panel" aria-labelledby="transcript-title">
          <div className="panel-heading"><h2 id="transcript-title">Transcript</h2><span>{call.turns.length} turns</span></div>
          <div className="transcript">
            {call.turns.map((turn, index) => <div className={`turn ${turn.role}`} key={`${turn.at_ms}-${index}`}>
              <div className="turn-meta"><span>{turn.role}</span><span>{turn.at_ms} ms</span>{turn.latency_ms !== null && <span className={turn.latency_ms > 1400 ? "degraded" : ""}>{turn.latency_ms} ms response</span>}</div>
              <div className="turn-text">{turn.text}</div>
              {turn.truncated_at_ms !== null && <div className="tool-chip">BARGE-IN / heard through {turn.truncated_at_ms} ms</div>}
            </div>)}
            {call.turns.length === 0 && <div className="empty">No transcript turns persisted.</div>}
          </div>
        </section>
        <aside className="panel" aria-labelledby="timeline-title">
          <div className="panel-heading"><h2 id="timeline-title">Event trail</h2><span>{call.events.length} events</span></div>
          <div className="timeline">
            {call.events.map((event, index) => <div className="timeline-item" key={`${event.at_ms}-${index}`}><div className="timeline-kind">{event.kind}</div><div className="timeline-detail">{event.at_ms} ms {JSON.stringify(event.payload)}</div></div>)}
            {call.events.length === 0 && <div className="empty">No events persisted.</div>}
          </div>
          <div className="panel-heading" style={{ marginTop: 20 }}><h2>Tool calls</h2><span>{call.tool_invocations.length}</span></div>
          {call.tool_invocations.map((tool, index) => <div className={`tool-chip ${tool.status === "ok" ? "" : "failed"}`} key={`${tool.name}-${index}`}>{tool.name} / {tool.status} / {tool.latency_ms} ms</div>)}
        </aside>
      </div>
    </div>
  );
}
