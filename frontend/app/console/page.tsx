"use client";

// Agent surface: the back-office case queue. Every case the assistant opened or handed off, ordered
// by the backend's triage (fraud first, then amount, then due time). The page renders what the API
// returns; priorities, facts and permissions are decided on the server.

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { ApiError, CaseDetail, CaseList, CaseSummary, claimCase, getCase, listCases, resolveCase } from "../../lib/api";

const PRIORITY_CLASS: Record<CaseSummary["priority"], string> = {
  Critical: "critical",
  High: "high",
  Medium: "medium",
  Low: "low",
};

const QUEUE_LABEL: Record<string, string> = { fraud: "Fraud", disputes: "Disputes", general: "General" };

function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.code ? `${err.code}: ${err.message}` : err.message;
  return err instanceof Error ? err.message : "Unknown error";
}

function dueText(c: CaseSummary): string {
  if (c.status === "resolved") return "resolved";
  const minutes = Math.round((new Date(c.due_at).getTime() - Date.now()) / 60000);
  const abs = Math.abs(minutes);
  const span = abs < 60 ? `${abs} min` : abs < 48 * 60 ? `${Math.round(abs / 60)} h` : `${Math.round(abs / 1440)} d`;
  return minutes < 0 ? `overdue ${span}` : `due in ${span}`;
}

function Badge({ text, tone }: { text: string; tone: string }) {
  return <span className={`badge ${tone}`}>{text}</span>;
}

function FactTable({ facts }: { facts: Record<string, unknown> | undefined }) {
  const entries = Object.entries(facts ?? {}).filter(([, v]) => v !== null && v !== undefined && v !== "");
  if (entries.length === 0) return <p className="muted">None</p>;
  return (
    <table className="facts">
      <tbody>
        {entries.map(([key, value]) => (
          <tr key={key}>
            <td>{key}</td>
            <td>{typeof value === "object" ? JSON.stringify(value) : String(value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ConsolePage() {
  const [credential, setCredential] = useState("");
  const [loggedIn, setLoggedIn] = useState(false);
  const [queue, setQueue] = useState<string>("");
  const [list, setList] = useState<CaseList | null>(null);
  const [selected, setSelected] = useState<CaseDetail | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (!loggedIn) return;
    try {
      setList(await listCases(credential.trim(), queue || undefined));
      setError(null);
    } catch (err) {
      setError(errorText(err));
      if (err instanceof ApiError && err.status === 401) setLoggedIn(false);
    }
  }, [credential, loggedIn, queue]);

  useEffect(() => {
    void refresh();
    if (!loggedIn) return;
    const timer = setInterval(() => void refresh(), 15000);
    return () => clearInterval(timer);
  }, [refresh, loggedIn]);

  async function run(action: () => Promise<CaseDetail>) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      setSelected(await action());
      setError(null);
      await refresh();
    } catch (err) {
      setError(errorText(err));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  function signIn(event: FormEvent) {
    event.preventDefault();
    if (!credential.trim()) {
      setError("Enter your staff credential.");
      return;
    }
    setError(null);
    setLoggedIn(true);
  }

  function onResolve(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    const text = note.trim();
    void run(async () => {
      const done = await resolveCase(credential.trim(), selected.case.case_id, text);
      setNote("");
      return done;
    });
  }

  if (!loggedIn) {
    return (
      <main>
        <div className="gate card">
          <h1>Agent console</h1>
          <p className="muted">Dispute and fraud cases prepared by the assistant. Sign in with your staff credential.</p>
          <form onSubmit={signIn}>
            <label>
              Staff credential
              <input value={credential} onChange={(e) => setCredential(e.target.value)} type="password" autoComplete="off" />
            </label>
            <button type="submit">Sign in</button>
          </form>
          {error ? <p role="alert">{error}</p> : null}
        </div>
      </main>
    );
  }

  const stats = list?.stats;
  return (
    <main className="console-page">
      <div className="console-head">
        <div>
          <h1>Agent console</h1>
          <span className="muted">
            Signed in as <strong>{list?.agent_id ?? "…"}</strong>
          </span>
        </div>
        <div className="row">
          <button type="button" className="quiet" onClick={() => void refresh()}>
            Refresh
          </button>{" "}
          <button
            type="button"
            className="quiet"
            onClick={() => {
              setLoggedIn(false);
              setCredential("");
              setList(null);
              setSelected(null);
            }}
          >
            Sign out
          </button>
        </div>
      </div>

      {stats ? (
        <section className="stats" aria-label="queue summary">
          <div className="stat">
            <span className="n">{stats.open_cases}</span>open cases
          </div>
          <div className="stat">
            <span className={`n${stats.overdue ? " bad" : ""}`}>{stats.overdue}</span>
            overdue
          </div>
          {(["Critical", "High", "Medium", "Low"] as const).map((p) => (
            <div key={p} className="stat">
              <span className={`n ${PRIORITY_CLASS[p]}`}>{stats.by_priority[p] ?? 0}</span>
              {p.toLowerCase()}
            </div>
          ))}
        </section>
      ) : null}

      <label>
        Queue{" "}
        <select value={queue} onChange={(e) => setQueue(e.target.value)}>
          <option value="">All</option>
          <option value="fraud">Fraud</option>
          <option value="disputes">Disputes</option>
          <option value="general">General</option>
        </select>
      </label>

      <div className="split">
        <section aria-label="cases" className="card">
          {list && list.cases.length === 0 ? <p>No cases yet. They appear here when the assistant opens or hands one off.</p> : null}
          <table>
            <thead>
              <tr>
                <th>Priority</th>
                <th>Case</th>
                <th>Queue</th>
                <th>Due</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {list?.cases.map((c) => (
                <tr
                  key={c.case_id}
                  onClick={() => void run(() => getCase(credential.trim(), c.case_id))}
                  className={`case-row${selected?.case.case_id === c.case_id ? " selected" : ""}${
                    c.status === "resolved" ? " resolved" : ""
                  }`}
                >
                  <td>
                    <Badge text={c.priority} tone={PRIORITY_CLASS[c.priority]} />
                  </td>
                  <td>
                    <div className="case-title">
                      {c.merchant ?? "—"} {c.amount_usd ? `· ${c.amount_usd} USD` : ""}
                    </div>
                    <div className="case-sub">{c.summary}</div>
                  </td>
                  <td>{QUEUE_LABEL[c.queue] ?? c.queue}</td>
                  <td className={c.overdue ? "overdue" : undefined}>{dueText(c)}</td>
                  <td>
                    {c.status}
                    {c.assigned_to ? ` · ${c.assigned_to}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section aria-label="case detail" className="card detail">
          {selected ? (
            <div>
              <h2>
                {selected.case.case_id} <Badge text={selected.case.priority} tone={PRIORITY_CLASS[selected.case.priority]} />
              </h2>
              <p>{selected.case.summary}</p>
              <p className="muted">
                Why this priority: {selected.triage_reason}. {dueText(selected.case)}.
                {selected.expected_resolution_days !== null
                  ? ` Similar cases took ${selected.expected_resolution_days} days (bank history, median).`
                  : ""}
              </p>

              <h3>Open questions</h3>
              <ul>
                {selected.open_questions.map((q) => (
                  <li key={q}>{q}</li>
                ))}
              </ul>

              <h3>Verified facts (bank records)</h3>
              <FactTable facts={selected.facts.verified} />

              {selected.facts.context ? (
                <>
                  <h3>Context read from the bank (customer, policy rule, search)</h3>
                  <FactTable facts={selected.facts.context} />
                </>
              ) : null}

              <h3>What the customer said</h3>
              <FactTable facts={selected.facts.customer_said} />

              <h3>Actions taken by the assistant</h3>
              {selected.actions.length ? (
                <ul>
                  {selected.actions.map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
              ) : (
                <p className="muted">None</p>
              )}

              <details>
                <summary>Tool audit trail ({selected.audit.length})</summary>
                <ul>
                  {selected.audit.map((a, i) => (
                    <li key={`${a.at}-${i}`}>
                      {new Date(a.at).toLocaleString()} · {a.tool} · {a.outcome}
                      {a.reason ? ` · ${a.reason}` : ""}
                    </li>
                  ))}
                </ul>
              </details>

              {selected.case.status === "resolved" ? (
                <p>
                  <strong>Resolved by {selected.case.assigned_to}:</strong> {selected.resolution_note}
                </p>
              ) : selected.case.status === "new" ? (
                <p>
                  <button type="button" disabled={busy} onClick={() => void run(() => claimCase(credential.trim(), selected.case.case_id))}>
                    Claim case
                  </button>
                </p>
              ) : (
                <form onSubmit={onResolve} className="resolve">
                  <label>
                    Resolution note (your decision and what you told the customer)
                    <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} />
                  </label>
                  <button type="submit" disabled={busy || note.trim().length < 3}>
                    Resolve case
                  </button>
                </form>
              )}
            </div>
          ) : (
            <p className="muted">Select a case to see the facts, open questions and actions.</p>
          )}
        </section>
      </div>
      {error ? <p role="alert">{error}</p> : null}
    </main>
  );
}
