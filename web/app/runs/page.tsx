"use client";
import { useEffect, useState } from "react";
import { call, Issue, RunSummary } from "@/lib/api";

export default function RunsPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [open, setOpen] = useState("");
  const [issues, setIssues] = useState<Issue[] | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => { call<RunSummary[]>("/runs").then(setRuns).catch((e) => setError(String(e))); }, []);

  async function inspect(id: string, model: boolean) {
    setOpen(id); setIssues(null);
    try { setIssues(await call<Issue[]>(`/runs/${id}/${model ? "review" : "lint"}`)); }
    catch (e) { setError(String(e)); }
  }
  async function rate(rating: "good" | "bad") {
    await call(`/runs/${open}/feedback`, { method: "POST", body: JSON.stringify({ rating, note }) });
    setNote(""); inspect(open, false);
  }

  return (
    <>
      <h1>Runs</h1>
      <p className="muted">Every plan and audit is traced. Review a run, mark it good or bad, and turn bad runs into eval cases.</p>
      {error && <div className="card high">{error}</div>}
      {runs.map((r) => (
        <div className="card" key={r.run_id}>
          <strong>{r.run_id}</strong> <small>{String(r.meta.kind ?? "audit")} · {String(r.meta.destination ?? "")} · {r.spans} spans · {r.llm_calls} LLM calls · {Math.round(r.total_ms)} ms</small>
          {r.errors.length > 0 && <div className="high">Errors in: {r.errors.join(", ")}</div>}
          <p>
            <button className="secondary" onClick={() => inspect(r.run_id, false)}>Quick checks</button>{" "}
            <button className="secondary" onClick={() => inspect(r.run_id, true)}>Model review</button>
          </p>
          {open === r.run_id && (
            <>
              {issues === null && <small>Loading…</small>}
              {issues?.length === 0 && <div className="ok card">No issues found.</div>}
              {issues?.map((i, k) => (
                <div key={k} className={`card ${i.severity === "high" ? "high" : i.severity === "medium" ? "warn" : ""}`}>
                  <b>{i.severity}</b> <small>({i.source}) {i.span}</small>
                  <div>{i.problem}</div><small>{i.suggestion}</small>
                </div>
              ))}
              <input type="text" placeholder="What was good or wrong?" value={note} onChange={(e) => setNote(e.target.value)} />{" "}
              <button onClick={() => rate("good")}>Good</button>{" "}
              <button className="secondary" onClick={() => rate("bad")}>Bad</button>
            </>
          )}
        </div>
      ))}
    </>
  );
}
