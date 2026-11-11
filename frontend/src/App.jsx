import { useCallback, useEffect, useState } from "react";
import { api, clearKey, getKey, setKey } from "./api";
import Requests from "./Requests";
import Approvals from "./Approvals";
import Agents from "./Agents";

function Login({ onDone }) {
  const [key, setK] = useState("");
  const [err, setErr] = useState("");

  async function submit(e) {
    e.preventDefault();
    setKey(key.trim());
    try {
      await api("/v1/agents"); // quick check that the key works
      onDone();
    } catch (ex) {
      clearKey();
      setErr(ex.message);
    }
  }

  return (
    <div className="login">
      <h1>Agent Wallet</h1>
      <p className="muted">Enter the owner (admin) key to open the dashboard.</p>
      <form onSubmit={submit}>
        <input type="password" placeholder="Admin key" value={key}
               onChange={(e) => setK(e.target.value)} autoFocus />
        <button className="btn primary">Open dashboard</button>
      </form>
      {err && <p className="error">{err}</p>}
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(!!getKey());
  const [tab, setTab] = useState("requests");
  const [requests, setRequests] = useState([]);
  const [agents, setAgents] = useState([]);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    try {
      const [r, a] = await Promise.all([
        api("/v1/payment-requests?limit=200"),
        api("/v1/agents"),
      ]);
      setRequests(r);
      setAgents(a);
      setError("");
    } catch (e) {
      if (e.status === 401) { clearKey(); setAuthed(false); }
      else setError(e.message);
    }
  }, []);

  useEffect(() => {
    if (!authed) return;
    reload();
    const t = setInterval(reload, 5000);
    return () => clearInterval(t);
  }, [authed, reload]);

  if (!authed) return <Login onDone={() => setAuthed(true)} />;

  const pending = requests.filter((r) => r.status === "PENDING_APPROVAL").length;
  const tabs = [
    ["requests", "Requests"],
    ["approvals", `Approvals${pending ? ` (${pending})` : ""}`],
    ["agents", "Agents"],
  ];

  return (
    <div className="app">
      <header>
        <h1>Agent Wallet</h1>
        <nav>
          {tabs.map(([id, label]) => (
            <button key={id} className={tab === id ? "tab active" : "tab"}
                    onClick={() => setTab(id)}>{label}</button>
          ))}
        </nav>
        <button className="btn" onClick={() => { clearKey(); setAuthed(false); }}>Log out</button>
      </header>

      {error && <p className="error">Could not reach the API: {error}</p>}

      {tab === "requests" && <Requests requests={requests} reload={reload} />}
      {tab === "approvals" && <Approvals requests={requests} reload={reload} />}
      {tab === "agents" && <Agents agents={agents} reload={reload} />}
    </div>
  );
}