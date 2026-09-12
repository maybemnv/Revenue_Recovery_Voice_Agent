"use client";

import { useEffect, useState } from "react";
import { api, ApiError, FixtureRole } from "../lib/api";

type Client = { client_id: string; display_name: string };

function saveError(error: unknown): string {
  if (!(error instanceof ApiError)) return "The fixture configuration was not saved. Try again.";
  if (typeof error.detail === "string") return error.detail;
  if (Array.isArray(error.detail)) {
    const messages = error.detail.flatMap((detail) => {
      if (!detail || typeof detail !== "object") return [];
      const { loc, msg } = detail as { loc?: unknown; msg?: unknown };
      if (!Array.isArray(loc) || typeof msg !== "string") return [];
      return [`${loc.filter((part) => typeof part === "string" || typeof part === "number").join(".")}: ${msg}`];
    });
    if (messages.length) return messages.join(" ");
  }
  return `The fixture configuration was not saved (HTTP ${error.status}).`;
}

export default function AgentPage() {
  const [role, setRole] = useState<FixtureRole>("viewer");
  const [client, setClient] = useState<Client | null>(null);
  const [config, setConfig] = useState("");
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    setError("");
    setStatus("");
    api<Client[]>("/clients", { fixtureRole: role })
      .then((clients) => {
        if (!clients[0]) throw new Error("No fixture client is available.");
        setClient(clients[0]);
        return api<object>(`/clients/${encodeURIComponent(clients[0].client_id)}/config`, { fixtureRole: role });
      })
      .then((value) => setConfig(JSON.stringify(value, null, 2)))
      .catch(() => {
        setClient(null);
        setError("Configuration is unavailable. Start the fixture API and try again.");
      })
      .finally(() => setLoading(false));
  }, [role]);

  async function save() {
    if (!client) return;
    setError("");
    setStatus("");
    let payload: object;
    try {
      payload = JSON.parse(config) as object;
    } catch {
      setError("Configuration must be valid JSON before it can be saved.");
      return;
    }
    try {
      const saved = await api<object>(`/fixture/clients/${encodeURIComponent(client.client_id)}/config`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
        fixtureRole: role,
      });
      setConfig(JSON.stringify(saved, null, 2));
      setStatus("Saved fixture configuration.");
    } catch (error) {
      setError(saveError(error));
    }
  }

  return <div className="page"><div className="page-heading"><div className="heading-copy"><div className="eyebrow">Fixture configuration {client ? `/ ${client.client_id}` : ""}</div><h1>Agent surface</h1><p className="subhead">Fixture edits are reset on replay and apply only to calls that start after the save.</p></div></div>{error && <div className="error-banner" role="alert">{error}</div>}<section className="panel"><div className="panel-heading"><h2>Change control</h2><span>fixture only / runtime reset</span></div><div className="filters"><label htmlFor="fixture-role">Fixture role</label><select id="fixture-role" value={role} onChange={(event) => setRole(event.target.value as FixtureRole)}><option value="viewer">Viewer</option><option value="admin">Admin</option></select></div>{loading ? <div className="placeholder">Loading fixture configuration.</div> : <><label htmlFor="fixture-config">Configuration JSON</label><textarea id="fixture-config" value={config} onChange={(event) => setConfig(event.target.value)} disabled={role !== "admin"} rows={20} /><div className="detail-actions"><button className="button" type="button" onClick={save} disabled={role !== "admin" || !client}>Save fixture configuration</button>{role === "viewer" && <span className="mono">Viewer is read-only; the API rejects writes too.</span>}</div></>}{status && <p role="status">{status}</p>}</section></div>;
}
