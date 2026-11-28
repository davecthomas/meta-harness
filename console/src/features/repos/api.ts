import { call } from "../../client";

// One repo's health, from GET /v1/repos/health: how its base stands, how long its checks usually take and which
// were re-run for failing by chance, the work there, what went live last, and its settings.
export interface RepoHealth {
  repo: string;
  registered: boolean;
  default_checks: string[];
  deploy_check: string;
  base: {
    state: "passing" | "failing";
    commit: string;
    checks: { command: string; passed: boolean; on: "sandbox" | "code_host" }[];
    failing: string[];
    goal_id: string;
    // The goal whose task ran the failing check, by its title.
    found_by: string;
  } | null;
  // The fix for a failing base: asked of its goal's operator, or a task under way.
  fix: { asked: boolean; goal_id: string; ref: string; task_id: string; open: boolean } | null;
  usual_seconds: number | null;
  reruns: { check: string; times: number }[];
  goals: { id: string; title: string }[];
  tasks: number;
  pulls: number;
  live: { task_id: string; title: string; at: string; deploy_check: string } | null;
}

export interface ReposHealth {
  repos: RepoHealth[];
  // How many repos' base fails its checks.
  failing: number;
}

export const reposApi = {
  reposHealth: () => call<ReposHealth>("/v1/repos/health"),
};
