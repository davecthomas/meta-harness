import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, issueUrl, type Goal, type IssueLink, type Task } from "../api";
import { useInfinite } from "../hooks";

export function GoalPage() {
  const { goalId = "" } = useParams();
  const [goal, setGoal] = useState<(Goal & { issues: IssueLink[] }) | null>(null);
  const [tasks, setTasks] = useState<Record<string, Task>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const g = await api.goal(goalId);
      setGoal(g);
      const page = await api.tasks({ goalId }, 0, 1000);
      setTasks(Object.fromEntries(page.items.map((t) => [t.id, t])));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [goalId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(action: () => Promise<unknown>) {
    try {
      await action();
      setError(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  // A goal's issues are all in one response; the table reads ahead through them 50 at a time.
  const issues = useMemo(() => goal?.issues ?? [], [goal]);
  const shown = useInfinite(
    async (offset, limit) => ({ items: issues.slice(offset, offset + limit), total: issues.length }),
    50,
    `${goalId}:${issues.length}`,
  );

  if (!goal) return error ? <div className="error">{error}</div> : <p className="empty">Loading…</p>;
  const open = !["met", "canceled"].includes(goal.status);

  return (
    <>
      <h1>{goal.statement}</h1>
      {error && <div className="error">{error}</div>}
      <div className="panel">
        <dl className="facts">
          <dt>Goal</dt><dd className="mono">{goal.id}</dd>
          <dt>Status</dt><dd className={`status-${goal.status}`}>{goal.status}</dd>
          <dt>Modality</dt><dd>{goal.modality}</dd>
          <dt>Label</dt><dd><span className="chip">{goal.label}</span></dd>
          <dt>Repos</dt><dd>{goal.repos.map((r) => <div className="mono" key={r}>{r}</div>)}</dd>
          <dt>Auto-merge</dt><dd>{goal.auto_merge ? "on" : "off: pull requests wait for you"}</dd>
          <dt>Budget</dt><dd>{goal.max_usd ? `$${goal.max_usd}` : "none"}</dd>
          <dt>Created by</dt><dd>{goal.created_by}, {goal.created_at.slice(0, 16).replace("T", " ")}</dd>
        </dl>
      </div>

      <h2>Actions</h2>
      <div className="actions">
        {goal.status === "active" && (
          <>
            <button onClick={() => act(() => api.goalAction(goal.id, "sweep"))}>Take in labeled issues now</button>
            <button className="quiet" onClick={() => act(() => api.goalAction(goal.id, "pause"))}>Pause</button>
          </>
        )}
        {["paused", "stalled", "budget_exhausted"].includes(goal.status) && (
          <button onClick={() => act(() => api.goalAction(goal.id, "resume"))}>Resume</button>
        )}
        {open && (
          <>
            <button className="quiet" onClick={() => act(() => api.patchGoal(goal.id, { auto_merge: !goal.auto_merge }))}>
              Turn auto-merge {goal.auto_merge ? "off" : "on"}
            </button>
            <button className="danger" onClick={() => act(() => api.goalAction(goal.id, "cancel"))}>Cancel goal</button>
          </>
        )}
      </div>

      <h2>Issues and tasks</h2>
      {issues.length ? (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Issue</th><th>Issue state</th><th>Task</th><th>Task status</th><th>Pull request</th><th>Waits on</th></tr></thead>
            <tbody>
              {shown.items.map((i) => {
                const task = i.task_id ? tasks[i.task_id] : undefined;
                const url = issueUrl(i.ref);
                return (
                  <tr key={i.ref}>
                    <td>{url ? <a href={url}>{i.ref}</a> : i.ref}</td>
                    <td>{i.state}</td>
                    <td>{i.task_id && <Link className="mono" to={`/tasks/${i.task_id}`}>{i.task_id}</Link>}</td>
                    <td className={`status-${task?.status ?? ""}`}>
                      {task?.status ?? (i.refused_reason ? "refused" : "")}
                      {i.refused_reason && <div className="hint">{i.refused_reason}</div>}
                    </td>
                    <td>{task?.pr_url && <a href={task.pr_url}>open</a>}</td>
                    <td>{i.depends_on.map((d) => <div className="mono" key={d}>{d}</div>)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="empty">
          No issues yet. Label an open issue <span className="chip">{goal.label}</span> in one of the goal's repos; the
          planner takes it in within a minute, or now with "Take in labeled issues now".
        </p>
      )}
      <div ref={shown.sentinel} />
    </>
  );
}
