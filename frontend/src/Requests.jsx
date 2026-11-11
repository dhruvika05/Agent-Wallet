import { useEffect, useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { api, fmtTime } from "./api";

const Badge = (p) => <span className={`badge ${p.value}`}>{p.value}</span>;

export default function Requests({ requests, reload }) {
  const [selectedId, setSelectedId] = useState(null);
  const [audit, setAudit] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const selected = requests.find((r) => r.id === selectedId);

  const columns = useMemo(() => [
    { field: "id", headerName: "ID", width: 80, filter: "agNumberColumnFilter" },
    { field: "created_at", headerName: "Time", width: 190, valueFormatter: (p) => fmtTime(p.value) },
    { field: "recipient_email", headerName: "Recipient", flex: 1, minWidth: 230 },
    {
      headerName: "Amount", width: 120, filter: "agNumberColumnFilter",
      valueGetter: (p) => Number(p.data.amount),            // display and sorting only
      valueFormatter: (p) => "$" + p.value.toFixed(2),
    },
    { field: "status", headerName: "Status", width: 180, cellRenderer: Badge },
    { field: "decision_reason_code", headerName: "Reason", width: 230 },
  ], []);

  // Load the audit trail whenever the selected request (or its status) changes
  useEffect(() => {
    if (!selectedId) { setAudit([]); return; }
    api(`/v1/audit-log?request_id=${selectedId}`)
      .then((rows) => setAudit(rows.slice().reverse())) // API is newest-first; show oldest-first
      .catch(() => setAudit([]));
  }, [selectedId, selected?.status]);

  async function refreshStatus() {
    setBusy(true);
    try {
      const r = await api("/v1/payment-requests-refresh-all", { method: "POST" });
      setMsg(`Checked ${r.checked}: ${r.paid} paid, ${r.failed} failed, ${r.still_processing} still processing`);
    } catch (e) {
      setMsg(e.message);
    }
    await reload();
    setBusy(false);
  }

  return (
    <section>
      <div className="toolbar">
        <h2>Payment requests</h2>
        <button className="btn" onClick={refreshStatus} disabled={busy}>
          {busy ? "Checking PayPal..." : "Check payout status"}
        </button>
      </div>
      {msg && <p className="muted">{msg}</p>}

      <div style={{ height: 380 }}>
        <AgGridReact
          rowData={requests}
          columnDefs={columns}
          defaultColDef={{ sortable: true, filter: true, resizable: true }}
          getRowId={(p) => String(p.data.id)}
          onRowClicked={(e) => setSelectedId(e.data.id)}
          rowStyle={{ cursor: "pointer" }}
        />
      </div>

      {selected ? (
        <div className="panel">
          <h3>Request #{selected.id}</h3>
          <p><b>Purpose (written by the agent, never used for decisions):</b> {selected.purpose}</p>
          <p><b>Decision:</b> {selected.decision_explanation}</p>
          {selected.paypal_batch_id && <p><b>PayPal batch:</b> {selected.paypal_batch_id}</p>}
          <h4>Audit trail</h4>
          <ol className="timeline">
            {audit.map((a) => (
              <li key={a.id}>
                <b>{a.event}</b> <span className="muted">{fmtTime(a.timestamp)}</span>
                <div className="muted small">{a.details_json ? JSON.stringify(a.details_json) : ""}</div>
              </li>
            ))}
          </ol>
        </div>
      ) : (
        <p className="muted">Click a row to see its full audit trail.</p>
      )}
    </section>
  );
}