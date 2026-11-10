import { call } from "../../client";

export type ConfigKind = "tiers" | "profiles" | "repos";

export interface ConfigSummary {
  kind: ConfigKind;
  title: string;
  description: string;
  version: number;
  edited_by: string;
  created_at: string;
}

export interface ConfigVersion {
  kind: ConfigKind;
  version: number;
  document: Record<string, any>;
  edited_by: string;
  note: string | null;
  origin: "seed" | "edit" | "rollback";
  rolled_back_from: number | null;
  created_at: string;
}

// The config feature's calls; src/api.ts spreads them into `api`.
export const configApi = {
  configKinds: () => call<{ kinds: ConfigSummary[]; source: "db" | "file" }>("/v1/config"),
  configModels: () =>
    call<{ models: { name: string; engine: string; model: string; status: string }[] }>("/v1/config/models").then((r) => r.models),
  config: (kind: ConfigKind) => call<ConfigVersion>(`/v1/config/${kind}`),
  configVersions: (kind: ConfigKind) =>
    call<{ versions: ConfigVersion[] }>(`/v1/config/${kind}/versions`).then((r) => r.versions),
  saveConfig: (kind: ConfigKind, document: unknown, note: string) =>
    call<ConfigVersion>(`/v1/config/${kind}`, { method: "PUT", body: JSON.stringify({ document, note: note || null }) }),
  rollbackConfig: (kind: ConfigKind, version: number) =>
    call<ConfigVersion>(`/v1/config/${kind}/rollback`, { method: "POST", body: JSON.stringify({ version }) }),
};
