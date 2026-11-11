import { useState } from "react";
import { api, fmtTime } from "./api";

export default function Approvals({ requests, reload }) {
  const [busyId, setBusyId] = useState(null);
  const [msg, setMsg] = useState("");
  const pending = requests.filter((r) => r.status === "PENDING_APPROVAL");

  async function act(id, action) {
    setBusyId(id);
    setMsg("");
    try {
      await api(`/v1/payment-requests/${id}/${action}`, {
        method: "POST",
        body: action === "reject" ? JSON.stringify({ note: "Rejected from dashboard" }) : undefined,
      });
    } catch (e) {
      setMsg(`Request #${id}: ${e.message}`); // e.g. the re-check blocked the approval
    }
    await reload();
    setBusyId(null);
  }

  return (
    <section>
      <h2>Waiting for your decision</h2>
      {msg && <p className="error">{msg}</p>}
      {pending.length === 0 && <p className="muted">Nothing is waiting. Held payments will appear here.</p>}
      <div className="cards">
        {pending.map((r) => (
          <div className="card" key={r.id}>
            <div className="amount">${Number(r.amount).toFixed(2)}</div>
            <p><b>To:</b> {r.recipient_email}</p>
            <p><b>Purpose:</b> {r.purpose}</p>
            <p className="muted">{r.decision_explanation}</p>
            <p className="muted small">Request #{r.id} · {fmtTime(r.created_at)}</p>
            <div className="row">
              <button className="btn primary" disabled={busyId === r.id}
                      onClick={() => act(r.id, "approve")}>Approve</button>
              <button className="btn danger" disabled={busyId === r.id}
                      onClick={() => act(r.id, "reject")}>Reject</button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}