import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, ApiError } from "./api";
import { Overview } from "./pages/Overview";
import { NewGoal } from "./pages/NewGoal";
import { GoalPage } from "./pages/GoalPage";
import { TasksPage } from "./pages/TasksPage";
import { TaskPage } from "./pages/TaskPage";

export function App() {
  const [who, setWho] = useState<string | null>(null);
  const [refused, setRefused] = useState<string | null>(null);

  useEffect(() => {
    api.me().then(
      (me) => setWho(me.identity),
      (err) => setRefused(err instanceof ApiError ? err.detail : String(err)),
    );
  }, []);

  return (
    <>
      <header className="top">
        <span className="brand">Meta-harness console</span>
        <nav>
          <NavLink to="/" end>Overview</NavLink>
          <NavLink to="/tasks">Tasks</NavLink>
          <NavLink to="/goals/new">New goal</NavLink>
        </nav>
        {who && <span className="who">Signed in as {who}</span>}
      </header>
      <main>
        {refused ? (
          <>
            <h1>Not allowed</h1>
            <p>{refused}</p>
          </>
        ) : who === null ? (
          <p className="empty">Signing in…</p>
        ) : (
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/goals/new" element={<NewGoal />} />
            <Route path="/goals/:goalId" element={<GoalPage />} />
            <Route path="/tasks" element={<TasksPage />} />
            <Route path="/tasks/:taskId" element={<TaskPage />} />
          </Routes>
        )}
      </main>
    </>
  );
}
