import { useCallback, useEffect, useState } from "react";
import { api, type RepoHealth, type ReposHealth } from "../../api";
import { useWorkChanged } from "../goals/goalSync";

// Every repo's health, read once and shared by whatever shows it: the Repos page, the sidebar's count and the
// marker beside a repo. It is read again when the work changes (goalSync's WORK_CHANGED), so they move together.
let kept: ReposHealth | null = null;
let reading: Promise<ReposHealth> | null = null;
const HEARD = "mh:repos-health";

function read(): Promise<ReposHealth> {
  reading ??= api.reposHealth().then(
    (found) => {
      // An answer that is not the health list (an older planner during a deploy) is read as no repos.
      const repos = Array.isArray(found?.repos) ? found.repos.filter((r) => typeof r?.repo === "string") : [];
      kept = { repos, failing: repos.filter((r) => r.base?.state === "failing").length };
      reading = null;
      window.dispatchEvent(new CustomEvent(HEARD));
      return kept;
    },
    (err) => {
      reading = null;
      throw err;
    },
  );
  return reading;
}

// What a test starts from: nothing kept.
export function forgetRepoHealth(): void {
  kept = null;
  reading = null;
}

export function useReposHealth(): { health: ReposHealth | null; error: string | null; reload: () => void } {
  const [health, setHealth] = useState<ReposHealth | null>(kept);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    read().then(
      () => setError(null),
      (err) => setError(err instanceof Error ? err.message : String(err)),
    );
  }, []);
  useEffect(() => {
    const heard = () => setHealth(kept);
    window.addEventListener(HEARD, heard);
    if (!kept) reload();
    return () => window.removeEventListener(HEARD, heard);
  }, [reload]);
  useWorkChanged(reload);
  return { health, error, reload };
}

export const healthOf = (health: ReposHealth | null, repo: string): RepoHealth | undefined =>
  health?.repos.find((r) => r.repo.toLowerCase() === repo.toLowerCase());

// A base in a few words: "Base passes", or "Base fails 3 checks". Empty for a base not checked yet.
export function baseWords(repo: RepoHealth | undefined): string {
  if (!repo?.base) return "";
  const n = repo.base.failing.length;
  return n ? `Base fails ${n} check${n === 1 ? "" : "s"}` : "Base passes";
}
