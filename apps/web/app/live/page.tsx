"use client";

import { useEffect, useState } from "react";
import type { CSSProperties } from "react";

type LiveEvent = { kind: string; call_id: string; at: string; [key: string]: unknown };

export default function LivePage() {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
    const source = new EventSource(`${base}/api/stream`);
    source.onopen = () => setConnected(true);
    source.onmessage = (message) => {
      try { setEvents((current) => [JSON.parse(message.data) as LiveEvent, ...current].slice(0, 12)); } catch { /* ignore malformed provider events */ }
    };
    source.onerror = () => setConnected(false);
    return () => source.close();
  }, []);

  return <div className="page">
    <div className="page-heading"><div><div className="eyebrow">Operations / live</div><h1>Live monitor</h1><p className="subhead">A read-only view of the media plane. The call keeps running if this tab disconnects.</p></div><div className="status"><span className="status-dot" /> {connected ? "SSE connected" : "Waiting for API"}</div></div>
    <div className="live-layout">
      <section className="live-screen" aria-label="Live call waveform"><div className="live-content"><div className="live-header"><h2>Audio activity</h2><span className="live-call-id">SSE / all calls</span></div><div className="wave" aria-hidden="true">{Array.from({ length: 32 }, (_, index) => <i key={index} style={{ "--h": `${18 + ((index * 31) % 90)}px` } as CSSProperties} />)}</div><div className="live-status"><span>{connected ? "Listening for call events" : "Reconnect pending"}</span><span>{events.length} buffered</span></div></div></section>
      <aside className="panel"><div className="panel-heading"><h2>Event stream</h2><span>newest first</span></div><div className="event-list">{events.map((event, index) => <div className="event-item" key={`${event.at}-${index}`}><div className="event-time">{new Date(event.at).toLocaleTimeString()}</div><div className="event-text">{event.kind} <span className="cell-meta">{event.call_id.slice(0, 8)}</span></div></div>)}{events.length === 0 && <div className="empty">No live call events yet.</div>}</div></aside>
    </div>
  </div>;
}
