"use client";
import { useState } from "react";
import { API, PlanResult, runJob } from "@/lib/api";

type Traveller = { name: string; age: number; mobility: "full" | "limited" };

export default function PlanPage() {
  const [dest, setDest] = useState("Kazakhstan");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [passport, setPassport] = useState("Indian");
  const [budget, setBudget] = useState("comfort");
  const [pace, setPace] = useState("balanced");
  const [interests, setInterests] = useState("nature, food");
  const [mustDo, setMustDo] = useState("");
  const [avoid, setAvoid] = useState("");
  const [people, setPeople] = useState<Traveller[]>([{ name: "", age: 30, mobility: "full" }]);
  const [busy, setBusy] = useState(false);
  const [ticks, setTicks] = useState(0);
  const [error, setError] = useState("");
  const [result, setResult] = useState<PlanResult | null>(null);

  const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
  const setPerson = (i: number, patch: Partial<Traveller>) =>
    setPeople(people.map((p, j) => (j === i ? { ...p, ...patch } : p)));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(""); setResult(null); setTicks(0);
    try {
      const brief = {
        destination: dest, start, end, passport, budget_tier: budget, pace,
        interests: list(interests), must_do: list(mustDo), avoid: list(avoid),
        travellers: people.map((p, i) => ({ ...p, name: p.name || `Traveller ${i + 1}` })),
      };
      setResult(await runJob<PlanResult>("/plan", { brief }, () => setTicks((t) => t + 1)));
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Plan a verified trip</h1>
      <p className="muted">Every activity is cited, checked against closures for your dates, and paced for the whole family.</p>
      <form onSubmit={submit}>
        <div className="grid2">
          <div><label>Where</label><input type="text" value={dest} onChange={(e) => setDest(e.target.value)} required /></div>
          <div><label>Passport</label><input type="text" value={passport} onChange={(e) => setPassport(e.target.value)} /></div>
          <div><label>Arrive</label><input type="date" value={start} onChange={(e) => setStart(e.target.value)} required /></div>
          <div><label>Depart</label><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} required /></div>
          <div><label>Budget</label>
            <select value={budget} onChange={(e) => setBudget(e.target.value)}>
              <option>budget</option><option>comfort</option><option>luxury</option>
            </select></div>
          <div><label>Pace</label>
            <select value={pace} onChange={(e) => setPace(e.target.value)}>
              <option>relaxed</option><option>balanced</option><option>packed</option>
            </select></div>
          <div><label>Interests (comma separated)</label><input type="text" value={interests} onChange={(e) => setInterests(e.target.value)} /></div>
          <div><label>Must do</label><input type="text" value={mustDo} onChange={(e) => setMustDo(e.target.value)} /></div>
          <div><label>Avoid</label><input type="text" value={avoid} onChange={(e) => setAvoid(e.target.value)} /></div>
        </div>
        <h2>Who is travelling</h2>
        {people.map((p, i) => (
          <div className="row" key={i}>
            <div><label>Name</label><input type="text" value={p.name} onChange={(e) => setPerson(i, { name: e.target.value })} /></div>
            <div><label>Age</label><input type="number" min={0} max={110} value={p.age} onChange={(e) => setPerson(i, { age: Number(e.target.value) })} /></div>
            <div><label>Mobility</label>
              <select value={p.mobility} onChange={(e) => setPerson(i, { mobility: e.target.value as "full" | "limited" })}>
                <option value="full">full</option><option value="limited">limited</option>
              </select></div>
            <button type="button" className="secondary" onClick={() => setPeople(people.filter((_, j) => j !== i))} disabled={people.length === 1}>Remove</button>
          </div>
        ))}
        <p>
          <button type="button" className="secondary" onClick={() => setPeople([...people, { name: "", age: 30, mobility: "full" }])}>Add traveller</button>{" "}
          <button type="submit" disabled={busy}>{busy ? `Researching… (${ticks * 1.5}s)` : "Build my plan"}</button>
        </p>
      </form>
      {error && <div className="card high">{error}</div>}
      {result && <Result r={result} />}
    </>
  );
}

function Result({ r }: { r: PlanResult }) {
  const { plan } = r;
  return (
    <>
      <h2>Your plan</h2>
      <p>
        Download: <a href={`${API}/packs/${r.run_id}/html`} target="_blank">printable page</a> ·{" "}
        <a href={`${API}/packs/${r.run_id}/pdf`} target="_blank">PDF</a> ·{" "}
        <a href={`${API}/packs/${r.run_id}/txt`} target="_blank">WhatsApp text</a> ·{" "}
        <a href={`${API}/packs/${r.run_id}/md`} target="_blank">markdown</a> · <small>run {r.run_id}</small>
      </p>
      {plan.warnings.length > 0 && (
        <div className="card warn"><strong>Check before you go</strong>
          <ul>{plan.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></div>
      )}
      {plan.days.map((d, i) => (
        <div className="card" key={d.day}>
          <strong>Day {i + 1}: {d.day}</strong> <small>({d.role}, effort {d.load}/{d.cap})</small>
          <ul>
            {d.blocks.map((b, j) => (
              <li key={j}><b>{b.start}–{b.end}</b> {b.title} {b.notes && <small>{b.notes}</small>}{" "}
                {b.sources.map((s) => <a key={s} href={s} target="_blank"><small>[source]</small></a>)}</li>
            ))}
          </ul>
          {d.plan_b && <small>{d.plan_b}</small>}
        </div>
      ))}
      <Claims title="Entry and visa" items={plan.visa} />
      <Claims title="Getting around" items={plan.transport} />
      <Claims title="Reports to be careful about" items={plan.avoid} />
    </>
  );
}

function Claims({ title, items }: { title: string; items: PlanResult["plan"]["visa"] }) {
  if (!items.length) return null;
  return (
    <div className="card"><strong>{title}</strong>
      <ul>{items.map((c, i) => <li key={i}>{c.text} <a href={c.evidence.url} target="_blank"><small>[source]</small></a></li>)}</ul></div>
  );
}
