"use client";

import { useEffect, useRef, useState } from "react";
import { WEBHOOK_BASE, formatNumber, type PracticeProfile } from "../lib/profile";

/** Two ways to call, zero friction: a number in large type for their desk phone,
 *  and a web-call button for laptop speakers on a Zoom share. */
export function CallPanel({ profile }: { profile: PracticeProfile }) {
  const [state, setState] = useState<"idle" | "connecting" | "live">("idle");
  const [error, setError] = useState<string | null>(null);
  const clientRef = useRef<{ stopCall: () => void } | null>(null);

  useEffect(() => () => clientRef.current?.stopCall(), []);

  async function startWebCall() {
    setError(null);
    setState("connecting");
    try {
      const response = await fetch(
        `${WEBHOOK_BASE}/demo/${profile.prospect_id}/web-call`,
        { method: "POST" },
      );
      if (!response.ok) throw new Error(`web call unavailable (${response.status})`);
      const { access_token: accessToken } = await response.json();
      if (!accessToken) throw new Error("no access token returned");

      const { RetellWebClient } = await import("retell-client-js-sdk");
      const client = new RetellWebClient();
      clientRef.current = client;
      client.on("call_started", () => setState("live"));
      client.on("call_ended", () => setState("idle"));
      client.on("error", () => {
        setError("The call dropped. Use the phone number instead.");
        setState("idle");
      });
      await client.startCall({ accessToken });
    } catch (err) {
      // Never leave the rep staring at a dead button mid-meeting — say what to
      // do instead, which is always "dial the number".
      setError(err instanceof Error ? err.message : "Could not start the call.");
      setState("idle");
    }
  }

  function stop() {
    clientRef.current?.stopCall();
    setState("idle");
  }

  const display = formatNumber(profile.demo_number) || profile.phone_display || "";

  return (
    <section className="call-panel">
      <div>
        <p className="call-label">Call this number</p>
        {profile.demo_number ? (
          <a className="call-number" href={`tel:${profile.demo_number}`}>
            {display}
          </a>
        ) : (
          <p className="call-number">{display || "Not provisioned yet"}</p>
        )}
      </div>

      <div className="call-actions">
        {state === "live" ? <span className="live-dot">On a call</span> : null}
        <button
          className={`web-call-btn ${state === "live" ? "active" : ""}`}
          onClick={state === "live" ? stop : startWebCall}
          disabled={state === "connecting" || !profile.retell_agent_id}
        >
          {state === "live"
            ? "End call"
            : state === "connecting"
              ? "Connecting…"
              : "Call from this browser"}
        </button>
      </div>

      {error ? (
        <p className="empty" role="alert" style={{ flexBasis: "100%" }}>
          {error}
        </p>
      ) : null}
    </section>
  );
}
