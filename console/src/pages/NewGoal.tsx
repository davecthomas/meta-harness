import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";

export function NewGoal() {
  const navigate = useNavigate();
  const [repos, setRepos] = useState<string[]>([]);
  const [statement, setStatement] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [label, setLabel] = useState("");
  const [budget, setBudget] = useState("");
  const [priority, setPriority] = useState(0);
  const [autoMerge, setAutoMerge] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.repos().then((r) => setRepos(r.map((x) => x.name)), (err) => setError(String(err.message ?? err)));
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!chosen.length) {
      setError("Pick at least one repo.");
      return;
    }
    setSaving(true);
    try {
      const goal = await api.createGoal({
        statement,
        repos: chosen,
        label,
        max_usd: budget || null,
        priority,
        auto_merge: autoMerge,
      });
      navigate(`/goals/${goal.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSaving(false);
    }
  }

  return (
    <>
      <h1>New goal</h1>
      {error && <div className="error">{error}</div>}
      <form className="stack panel" onSubmit={submit}>
        <label className="field">
          Statement
          <span className="hint">What the goal is. The planner reads it.</span>
          <textarea required value={statement} onChange={(e) => setStatement(e.target.value)} />
        </label>
        <fieldset>
          <legend>Repos</legend>
          {repos.map((name) => (
            <label key={name}>
              <input
                type="checkbox"
                checked={chosen.includes(name)}
                onChange={(e) => setChosen(e.target.checked ? [...chosen, name] : chosen.filter((c) => c !== name))}
              />
              <span className="mono">{name}</span>
            </label>
          ))}
        </fieldset>
        <label className="field">
          Label
          <span className="hint">An open issue with this label, in any of the goal's repos, becomes one of its tasks.</span>
          <input type="text" required placeholder="meta-harness:bootstrap" value={label} onChange={(e) => setLabel(e.target.value)} />
        </label>
        <label className="field">
          Budget, in dollars
          <span className="hint">Optional. Admission holds the goal's tasks once its runs have spent this much.</span>
          <input type="number" min="0" step="0.01" value={budget} onChange={(e) => setBudget(e.target.value)} />
        </label>
        <label className="field">
          Priority
          <input type="number" value={priority} onChange={(e) => setPriority(Number(e.target.value))} />
        </label>
        <fieldset>
          <legend>Merging</legend>
          <label>
            <input type="checkbox" checked={autoMerge} onChange={(e) => setAutoMerge(e.target.checked)} />
            Merge pull requests without me
          </label>
        </fieldset>
        <div className="actions">
          <button type="submit" disabled={saving}>{saving ? "Creating…" : "Create goal"}</button>
        </div>
      </form>
    </>
  );
}
