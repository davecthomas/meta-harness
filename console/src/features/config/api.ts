import { call } from "../../client";

export type ConfigKind = "tiers" | "profiles" | "repos" | "trackers" | "skills" | "planning" | "task_policy";

// A skill as the skills configuration keeps it: what it is for, where it came from, and its files.
export interface SkillEntry {
  description: string;
  source: { kind: "git" | "upload" | "shipped"; url?: string; ref?: string; path?: string; commit?: string; by?: string };
  files: Record<string, string>;
}

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
  // max_sandboxes: the most sandboxes the deployment's VM holds, the ceiling on max_parallel; null when unset.
  configKinds: () => call<{ kinds: ConfigSummary[]; source: "db" | "file"; max_sandboxes?: number | null }>("/v1/config"),
  configModels: () =>
    call<{ models: { name: string; engine: string; model: string; status: string }[] }>("/v1/config/models").then((r) => r.models),
  // The harness images the deployment builds, which a profile's image is picked from; default: the image a
  // profile with no image runs. A 422 says why they cannot be listed.
  configImages: () =>
    call<{ images: { name: string; directory: string; default: boolean }[]; default: string }>("/v1/config/images"),
  // Read a skill from a git repository or from its files, to look at before it is staged. Saves nothing.
  readSkill: (from: { url?: string; ref?: string; path?: string; files?: Record<string, string> }) =>
    call<{ name: string; skill: SkillEntry }>("/v1/config/skills/read", { method: "POST", body: JSON.stringify(from) }),
  config: (kind: ConfigKind) => call<ConfigVersion>(`/v1/config/${kind}`),
  configVersions: (kind: ConfigKind) =>
    call<{ versions: ConfigVersion[] }>(`/v1/config/${kind}/versions`).then((r) => r.versions),
  saveConfig: (kind: ConfigKind, document: unknown, note: string) =>
    call<ConfigVersion>(`/v1/config/${kind}`, { method: "PUT", body: JSON.stringify({ document, note: note || null }) }),
  rollbackConfig: (kind: ConfigKind, version: number) =>
    call<ConfigVersion>(`/v1/config/${kind}/rollback`, { method: "POST", body: JSON.stringify({ version }) }),
};
