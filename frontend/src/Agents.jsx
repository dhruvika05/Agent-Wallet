import { useEffect, useState } from "react";
import { api } from "./api";

function Bar({ label, spent, limit }) {
  const s = Number(spent), l = Number(limit); // display only
  const pct = l > 0 ? Math.min(100, (s / l) * 100) : 0;
  return (
    <div className="bar">
      <div className="small">{label}: ${s.toFixed(2)} of ${l.toFixed(2)}</div>
      <div className="track"><div className={pct > 80 ? "fill hot" : "fill"} style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

function AgentCard({ agent, reload }) {
  const [vendors, setVendors] = useState([]);
  const [busy, setBusy] = useState(false);
  const frozen = agent.status === "frozen";
  const p = agent.policy;

  useEffect(() => {
    api(`/v1/agents/${agent.id}/recipients`).then(setVendors).catch(() => {});
  }, [agent.id]);

  async function toggle() {
    setBusy(true);
    await api(`/v1/agents/${agent.id}/${frozen ? "unfreeze" : "freeze"}`, { method: "POST" });
    await reload();
    setBusy(false);
  }

  return (
    <div className="card wide">
      <div className="row between">
        <h3>{agent.name}</h3>
        <span className={`badge ${frozen ? "FROZEN" : "ACTIVE"}`}>{agent.status.toUpperCase()}</span>
      </div>

      {p && (
        <>
          <Bar label="Today" spent={agent.spent_today} limit={p.daily_limit} />
          <Bar label="This week" spent={agent.spent_this_week} limit={p.weekly_limit} />
          <p className="small muted">
            Max per payment ${p.per_transaction_limit} · Human approval above ${p.approval_threshold} ·
            Max {p.max_requests_per_hour} requests/hour
          </p>
        </>
      )}

      <p className="small"><b>Approved vendors:</b> {vendors.map((v) => v.name).join(", ") || "none"}</p>
      <p className="small"><b>Waiting for approval:</b> {agent.pending_count}</p>

      <button className={frozen ? "btn primary big" : "btn danger big"} disabled={busy} onClick={toggle}>
        {frozen ? "Unfreeze agent" : "FREEZE AGENT (kill switch)"}
      </button>
    </div>
  );
}

export default function Agents({ agents, reload }) {
  return (
    <section>
      <h2>Agents</h2>
      <div className="cards">
        {agents.map((a) => <AgentCard key={a.id} agent={a} reload={reload} />)}
      </div>
    </section>
  );
}