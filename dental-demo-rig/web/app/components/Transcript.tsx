"use client";

import type { DemoCall } from "../lib/profile";

type Turn = { who: "agent" | "caller"; text: string };

/** Retell delivers the transcript as a single `Agent: … User: …` block. */
function parseTurns(transcript?: string | null): Turn[] {
  if (!transcript) return [];
  const parts = transcript.split(/\n?(?=(?:Agent|User|Assistant):)/g);
  return parts
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const [, speaker, body] = /^(Agent|Assistant|User):\s*([\s\S]*)$/.exec(part) ?? [];
      if (!speaker) return { who: "agent" as const, text: part };
      return { who: speaker === "User" ? ("caller" as const) : ("agent" as const), text: body };
    })
    .filter((turn) => turn.text.length > 0);
}

export function Transcript({ call }: { call?: DemoCall }) {
  const turns = parseTurns(call?.transcript);
  return (
    <section className="card" aria-label="Call transcript" aria-live="polite">
      <h2>Live transcript</h2>
      {turns.length === 0 ? (
        <p className="empty">
          Nothing yet. Call the number and the conversation appears here.
        </p>
      ) : (
        <div className="transcript">
          {turns.map((turn, index) => (
            <div className={`turn ${turn.who}`} key={index}>
              <div className="who">{turn.who === "agent" ? "Reception" : "Caller"}</div>
              <p>{turn.text}</p>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export function SummaryCard({ call }: { call?: DemoCall }) {
  if (!call?.summary) return null;
  return (
    <section className="card summary" aria-label="Call summary">
      <h2>Call summary</h2>
      <p style={{ margin: 0 }}>{call.summary}</p>
      {call.sentiment ? (
        <p style={{ margin: "10px 0 0" }}>
          <span className="tool-chip">{call.sentiment}</span>
        </p>
      ) : null}
    </section>
  );
}
