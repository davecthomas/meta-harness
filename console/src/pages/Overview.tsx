import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, issueUrl, type KillSwitch } from "../api";
import { useInfinite } from "../hooks";

const OPEN_GOALS = ["active", "paused", "stalled", "budget_exhausted"];
const NEEDS_YOU = ["ready", "failed", "review_unresolved"];

export function Overview() {
  const goals = useInfinite((offset, limit) => api.goals(OPEN_GOALS, offset, limit), 25, "open-goals");
  const waiting = useInfinite((offset, limit) => api.tasks({ statuses: NEEDS_YOU }, offset, limit), 25, "needs-you");
  const running = useInfinite((offset, limit) => api.tasks({ statuses: ["running"] }, offset, limit), 1, "running");

  return (
    <>
      <h1>Overview</h1>
      <KillSwitchPanel />
      <p>
        {running.total ?? "…"} running. <Link to="/tasks?view=tiles&size=16">Watch them</Link>
      </p>

      <h2>Needs you</h2>
      {waiting.error && <div className="error">{waiting.error}</div>}
      {waiting.items.length ? (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Task</th><th>Status</th><th>Issue</th><th>Pull request</th><th>Why</th></tr></thead>
            <tbody>
              {waiting.items.map((t) => {
                const url = issueUrl(t.external_ref);
                return (
                  <tr key={t.id}>
                    <td><Link to={`/tasks/${t.id}`}>{t.title}</Link><div className="mono">{t.id}</div></td>
                    <td className={`status-${t.status}`}>{t.status}</td>
                    <td>{url ? <a href={url}>{t.external_ref}</a> : <span className="mono">{t.external_ref}</span>}</td>
                    <td>{t.pr_url && <a href={t.pr_url}>open</a>}</td>
                    <td>{t.status === "ready" ? "its pull request is open for you to merge" : t.status_reason ?? t.status}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        waiting.total !== null && <p className="empty">Nothing waits on you.</p>
      )}
      <div ref={waiting.sentinel} />

      <h2>Open goals</h2>
      {goals.error && <div className="error">{goals.error}</div>}
      {goals.items.length ? (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Goal</th><th>Status</th><th>Label</th><th>Repos</th><th>Auto-merge</th><th>Created</th></tr></thead>
            <tbody>
              {goals.items.map((g) => (
                <tr key={g.id}>
                  <td><Link to={`/goals/${g.id}`}>{g.statement}</Link><div className="mono">{g.id}</div></td>
                  <td className={`status-${g.status}`}>{g.status}</td>
                  <td><span className="chip">{g.label}</span></td>
                  <td>{g.repos.map((r) => <div className="mono" key={r}>{r}</div>)}</td>
                  <td>{g.auto_merge ? "on" : "off"}</td>
                  <td>{g.created_at.slice(0, 16).replace("T", " ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        goals.total !== null && <p className="empty">No open goals. <Link to="/goals/new">Create one.</Link></p>
      )}
      <div ref={goals.sentinel} />
    </>
  );
}

function KillSwitchPanel() {
  const [state, setState] = useState<KillSwitch | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.killSwitch().then(setState, (err) => setError(String(err.message ?? err)));
  }, []);

  async function flip(event: FormEvent) {
    event.preventDefault();
    if (!state) return;
    try {
      setState(await api.setKillSwitch(!state.engaged, reason || (state.engaged ? "lifted" : "stopped from the console")));
      setReason("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (error) return <div className="error">Kill switch: {error}</div>;
  if (!state) return null;
  return (
    <form className={`kill ${state.engaged ? "engaged" : ""}`} onSubmit={flip}>
      <strong>{state.engaged ? "Kill switch is ON: nothing runs" : "Kill switch is off"}</strong>
      <span className="hint">
        {state.changed_by}, {state.changed_at.slice(0, 16).replace("T", " ")}{state.reason ? `: ${state.reason}` : ""}
      </span>
      <input type="text" placeholder="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />
      <button className={state.engaged ? "" : "danger"} type="submit">
        {state.engaged ? "Lift the kill switch" : "Stop everything"}
      </button>
    </form>
  );
}
