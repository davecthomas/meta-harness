import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, issueUrl, type MhEvent, type Task } from "../api";
import { useEventStream } from "../hooks";
import { describe, fold, type Line, type TileFacts } from "../terminal";
import { Terminal } from "../components/Terminal";

const FIRST = 400;
const OLDER = 200;
const EMPTY: TileFacts = { turn: 0, usd: 0, model: "", status: null };

// One task: its facts, cancel, and its run as a live terminal. The terminal opens at the newest
// events and loads older ones as you scroll up.
export function TaskPage() {
  const { taskId = "" } = useParams();
  const [task, setTask] = useState<Task | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [facts, setFacts] = useState<TileFacts>(EMPTY);
  const [from, setFrom] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const oldest = useRef<number | null>(null);
  const newest = useRef(0);
  const loadingOlder = useRef(false);
  const exhausted = useRef(false);

  useEffect(() => {
    let stale = false;
    setLines([]);
    setFacts(EMPTY);
    setFrom(null);
    exhausted.current = false;
    (async () => {
      try {
        const [t, page] = await Promise.all([api.task(taskId), api.events([taskId], FIRST)]);
        if (stale) return;
        setTask(t);
        setLines(page.events.map(describe).filter((l): l is Line => l !== null));
        setFacts(page.events.reduce(fold, EMPTY));
        oldest.current = page.events[0]?.position ?? null;
        exhausted.current = page.events.length < FIRST;
        newest.current = Math.max(page.events.at(-1)?.position ?? 0, page.latest_position);
        setFrom(newest.current);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      stale = true;
    };
  }, [taskId]);

  const onEvent = useCallback((e: MhEvent) => {
    if (e.position <= newest.current) return;
    newest.current = e.position;
    setFacts((f) => fold(f, e));
    if (e.event_type === "task_status_changed") setTask((t) => (t ? { ...t, status: e.payload.to } : t));
    if (e.event_type === "pull_request_opened") setTask((t) => (t ? { ...t, pr_url: e.payload.pr_url } : t));
    const line = describe(e);
    if (line) setLines((current) => [...current, line]);
  }, []);

  const state = useEventStream(from === null ? [] : [taskId], from, onEvent);

  const loadOlder = useCallback(async () => {
    if (loadingOlder.current || exhausted.current || oldest.current === null) return;
    loadingOlder.current = true;
    try {
      const page = await api.events([taskId], OLDER, oldest.current);
      exhausted.current = page.events.length < OLDER;
      if (page.events.length) {
        oldest.current = page.events[0].position;
        const older = page.events.map(describe).filter((l): l is Line => l !== null);
        setLines((current) => [...older, ...current]);
      }
    } finally {
      loadingOlder.current = false;
    }
  }, [taskId]);

  if (!task) return error ? <div className="error">{error}</div> : <p className="empty">Loading…</p>;
  const url = issueUrl(task.external_ref);
  const status = facts.status ?? task.status;

  async function cancel() {
    try {
      setTask(await api.cancelTask(task!.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      <h1>{task.title}</h1>
      {error && <div className="error">{error}</div>}
      <div className="panel">
        <dl className="facts">
          <dt>Task</dt><dd className="mono">{task.id}</dd>
          <dt>Status</dt>
          <dd>
            <span className={`status-${status}`}>{status}</span>
            {task.status_reason && `: ${task.status_reason}`}
            {task.cancel_requested && status === "running" && " (cancel requested)"}
          </dd>
          <dt>Repo</dt><dd className="mono">{task.repo}</dd>
          <dt>Issue</dt><dd>{url ? <a href={url}>{task.external_ref}</a> : <span className="mono">{task.external_ref}</span>}</dd>
          {task.goal_id && (<><dt>Goal</dt><dd><Link className="mono" to={`/goals/${task.goal_id}`}>{task.goal_id}</Link></dd></>)}
          <dt>Pull request</dt><dd>{task.pr_url ? <a href={task.pr_url}>{task.pr_url}</a> : "none yet"}</dd>
          <dt>Checks</dt><dd>{task.acceptance_checks.map((c) => <div className="mono" key={c}>{c}</div>)}</dd>
          <dt>Turn and spend</dt><dd>turn {facts.turn}, ${facts.usd.toFixed(4)} {facts.model && <span className="mono">{facts.model}</span>}</dd>
        </dl>
      </div>
      {["queued", "blocked", "running", "ready", "review_unresolved"].includes(status) && (
        <>
          <h2>Actions</h2>
          <div className="actions"><button className="danger" onClick={cancel}>Cancel task</button></div>
        </>
      )}
      <h2>Run <span className="chip">{state}</span></h2>
      <Terminal lines={lines} className="tall" onScrollTop={loadOlder} />
    </>
  );
}
