import { call, query } from "../../client";

// One log entry, as the Logs page shows it, with the whole Cloud Logging entry.
export interface LogEntry {
  time: string;
  service: string;
  severity: string;
  message: string;
  entry: Record<string, unknown>;
}

// One of the console's users, as the Users page shows it.
export interface ConsoleUser {
  email: string;
  role: "admin" | "user";
  status: "invited" | "active" | "deprovisioned";
  invited_by: string;
  invited_at: string;
  last_sign_in_at: string | null;
}

// The admin feature's calls; src/api.ts spreads them into `api`.
export const adminApi = {
  logs: (params: { unit?: string; severity?: string; minutes?: number; q?: string }) =>
    call<{ entries: LogEntry[]; units: string[] }>("/v1/admin/logs" + query(params)),
  deploys: () => call<{ version: string; commit: string | null; revision: string | null; project?: string | null }>("/v1/admin/deploys"),
  // The console's users: list them, invite one, set a role, end one's access.
  users: () => call<{ users: ConsoleUser[] }>("/v1/admin/users").then((r) => r.users),
  inviteUser: (email: string, role: string) => call<ConsoleUser>("/v1/admin/users", { method: "POST", body: JSON.stringify({ email, role }) }),
  setUserRole: (email: string, role: string) =>
    call<ConsoleUser>(`/v1/admin/users/${encodeURIComponent(email)}`, { method: "PATCH", body: JSON.stringify({ role }) }),
  deprovisionUser: (email: string) => call<ConsoleUser>(`/v1/admin/users/${encodeURIComponent(email)}`, { method: "DELETE" }),
  reposAvailable: () => call<{ repos: { name: string; private: boolean }[]; install_urls: string[] }>("/v1/repos/available"),
};
