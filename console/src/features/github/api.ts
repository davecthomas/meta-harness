import { call } from "../../client";
import type { ActsAsShown } from "../goals/ActsAs";

// The signed-in operator's own GitHub connection, from GET /v1/github/connection. Never the token.
export interface GitHubConnection {
  // Whether this deployment has the OAuth app operators connect through, and why not when it has none.
  enabled: boolean;
  why_off: string | null;
  // none: never connected. connected. broken: GitHub refused the token, so connect again.
  state: "none" | "connected" | "broken";
  connected: boolean;
  login: string | null;
  scopes: string;
  connected_at: string | null;
  // The accounts and organizations the operator chose to use. Empty: every one the connection reaches.
  owners: string[];
}

// What the connection reaches, from GET /v1/repos/reachable: every account and organization with its repo
// count, and the ones the operator chose.
export interface Reachable {
  repos: { name: string; access: "write" | "read" }[];
  owners: { name: string; repos: number }[];
  chosen: string[];
  stale: boolean;
  why: string | null;
}

// The GitHub feature's calls; src/api.ts spreads them into `api`.
export const githubApi = {
  githubConnection: () => call<GitHubConnection>("/v1/github/connection"),
  // Starts a sign-in: where to send the browser.
  githubConnect: () => call<{ url: string }>("/v1/github/connect", { method: "POST" }),
  // Finishes it, with what GitHub sent the browser back with.
  githubFinish: (code: string, state: string) => call<GitHubConnection>("/v1/github/connection", { method: "POST", body: JSON.stringify({ code, state }) }),
  // The accounts the connection reaches, without their repos: the list of repos can be hundreds long.
  githubReachable: () => call<Reachable>("/v1/repos/reachable?repos=0"),
  // Limit the connection to these accounts and organizations; none for all it reaches.
  githubChooseOwners: (owners: string[]) => call<GitHubConnection>("/v1/github/connection/owners", { method: "PUT", body: JSON.stringify({ owners }) }),
  // Whose GitHub access a goal uses, and taking it over as the signed-in operator.
  goalActsAs: (goalId: string) => call<ActsAsShown>(`/v1/goals/${goalId}/acts-as`),
  goalActAsMe: (goalId: string) => call<ActsAsShown>(`/v1/goals/${goalId}/act-as-me`, { method: "POST" }),
  githubDisconnect: () => call<GitHubConnection>("/v1/github/connection", { method: "DELETE" }),
};
