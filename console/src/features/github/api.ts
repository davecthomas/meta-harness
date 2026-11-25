import { call } from "../../client";

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
}

// The GitHub feature's calls; src/api.ts spreads them into `api`.
export const githubApi = {
  githubConnection: () => call<GitHubConnection>("/v1/github/connection"),
  // Starts a sign-in: where to send the browser.
  githubConnect: () => call<{ url: string }>("/v1/github/connect", { method: "POST" }),
  // Finishes it, with what GitHub sent the browser back with.
  githubFinish: (code: string, state: string) => call<GitHubConnection>("/v1/github/connection", { method: "POST", body: JSON.stringify({ code, state }) }),
  githubDisconnect: () => call<GitHubConnection>("/v1/github/connection", { method: "DELETE" }),
};
