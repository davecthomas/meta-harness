import { call, query } from "../../client";

// One log entry, as the Logs page shows it, with the whole Cloud Logging entry.
export interface LogEntry {
  time: string;
  service: string;
  severity: string;
  message: string;
  entry: Record<string, unknown>;
}

// The admin feature's calls; src/api.ts spreads them into `api`.
export const adminApi = {
  logs: (params: { unit?: string; severity?: string; minutes?: number; q?: string }) =>
    call<{ entries: LogEntry[]; units: string[] }>("/v1/admin/logs" + query(params)),
  deploys: () => call<{ version: string; commit: string | null; revision: string | null; project?: string | null }>("/v1/admin/deploys"),
  reposAvailable: () => call<{ repos: { name: string; private: boolean }[]; install_urls: string[] }>("/v1/repos/available"),
};
