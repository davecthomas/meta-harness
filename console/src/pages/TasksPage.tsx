import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, issueUrl } from "../api";
import { useInfinite } from "../hooks";
import { TileGrid, type TileSize } from "../components/TileGrid";

const FILTERS: [string, string, string[]][] = [
  ["running", "running", ["running"]],
  ["waiting", "queued and blocked", ["queued", "blocked"]],
  ["ready", "pull request open", ["ready", "review_unresolved"]],
  ["ended", "ended", ["closed", "failed", "canceled"]],
  ["all", "all", []],
];
const SIZES: TileSize[] = [4, 16, 32];

// Running tasks as a list or as terminal tiles, 4, 16 or 32 up. Both read ahead as you scroll.
export function TasksPage() {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "tiles" ? "tiles" : "list";
  const size = (SIZES.find((s) => String(s) === params.get("size")) ?? 16) as TileSize;
  const filter = FILTERS.find(([key]) => key === (params.get("show") ?? "running")) ?? FILTERS[0];
  const [streamState, setStreamState] = useState("idle");

  const tasks = useInfinite(
    (offset, limit) => api.tasks({ statuses: filter[2] }, offset, limit),
    view === "tiles" ? size : 50,
    `${filter[0]}:${view}:${size}`,
  );

  function set(next: Record<string, string>) {
    setParams({ view, size: String(size), show: filter[0], ...next });
  }

  return (
    <>
      <h1>Tasks</h1>
      <div className="toolbar">
        <div className="group">
          Show:
          {FILTERS.map(([key, label]) => (
            <button key={key} className={`link ${key === filter[0] ? "on" : ""}`} onClick={() => set({ show: key })}>{label}</button>
          ))}
        </div>
        <div className="group">
          View:
          <button className={`link ${view === "list" ? "on" : ""}`} onClick={() => set({ view: "list" })}>list</button>
          {SIZES.map((s) => (
            <button key={s} className={`link ${view === "tiles" && size === s ? "on" : ""}`} onClick={() => set({ view: "tiles", size: String(s) })}>
              {s} up
            </button>
          ))}
        </div>
        {view === "tiles" && <span className="chip">{streamState}</span>}
        {tasks.total !== null && <span className="hint">{tasks.items.length} of {tasks.total}</span>}
      </div>
      {tasks.error && <div className="error">{tasks.error}</div>}
      {tasks.total === 0 && <p className="empty">No {filter[1]} tasks.</p>}

      {view === "tiles" ? (
        <TileGrid tasks={tasks.items} size={size} onStreamState={setStreamState} />
      ) : (
        tasks.items.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Task</th><th>Status</th><th>Repo</th><th>Issue</th><th>Pull request</th><th>Updated</th></tr></thead>
              <tbody>
                {tasks.items.map((t) => {
                  const url = issueUrl(t.external_ref);
                  return (
                    <tr key={t.id}>
                      <td><Link to={`/tasks/${t.id}`}>{t.title}</Link><div className="mono">{t.id}</div></td>
                      <td className={`status-${t.status}`}>{t.status}</td>
                      <td className="mono">{t.repo}</td>
                      <td>{url ? <a href={url}>{t.external_ref}</a> : <span className="mono">{t.external_ref}</span>}</td>
                      <td>{t.pr_url && <a href={t.pr_url}>open</a>}</td>
                      <td>{t.updated_at.slice(0, 16).replace("T", " ")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )
      )}
      <div ref={tasks.sentinel} className="sentinel">{!tasks.done && tasks.total !== null && "Loading more…"}</div>
    </>
  );
}
