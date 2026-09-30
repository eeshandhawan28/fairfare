"use client";
import { useState } from "react";
import { AuditResult, runJob } from "@/lib/api";

export default function AuditPage() {
  const [quote, setQuote] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [dest, setDest] = useState("Kazakhstan");
  const [live, setLive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [res, setRes] = useState<AuditResult | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(""); setRes(null);
    try {
      setRes(await runJob<AuditResult>("/audit", {
        quote_text: quote, live, brief: { destination: dest, start, end, travellers: [] },
      }));
    } catch (err) { setError(String(err)); } finally { setBusy(false); }
  }

  return (
    <>
      <h1>Audit a travel-agent quote</h1>
      <p className="muted">Paste the quote as text. We check each line against independent prices and closures for your dates.</p>
      <form onSubmit={submit}>
        <label>Quote</label>
        <textarea value={quote} onChange={(e) => setQuote(e.target.value)} required />
        <div className="grid2">
          <div><label>Destination</label><input type="text" value={dest} onChange={(e) => setDest(e.target.value)} /></div>
          <div />
          <div><label>Arrive</label><input type="date" value={start} onChange={(e) => setStart(e.target.value)} required /></div>
          <div><label>Depart</label><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} required /></div>
        </div>
        <p><label><input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> Research the web for current prices and closures</label></p>
        <button type="submit" disabled={busy}>{busy ? "Auditing…" : "Audit quote"}</button>
      </form>
      {error && <div className="card high">{error}</div>}
      {res && (
        <>
          <h2>Findings</h2>
          {res.findings.length === 0 && <div className="card ok">No issues found.</div>}
          {[...res.findings].sort((a, b) => ord(a.severity) - ord(b.severity)).map((f, i) => (
            <div key={i} className={`card ${f.severity === "high" ? "high" : f.severity === "warn" ? "warn" : ""}`}>
              <strong>{f.severity === "high" ? "Act now" : f.severity === "warn" ? "Check" : "Note"}</strong>{" "}
              <small>{f.kind}, {f.confidence} confidence</small>
              <div><b>{f.line}</b>: {f.message}</div>
              {f.source && <small>Source: {f.source}</small>}
            </div>
          ))}
          <small>Run {res.run_id}</small>
        </>
      )}
    </>
  );
}
const ord = (s: string) => ({ high: 0, warn: 1, info: 2 } as Record<string, number>)[s] ?? 3;
