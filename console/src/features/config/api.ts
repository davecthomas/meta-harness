import { call, query } from "../../client";

export type ConfigKind = "tiers" | "profiles" | "repos" | "trackers" | "skills" | "planning" | "task_policy";

// A skill as the skills configuration keeps it: what it is for, where it came from, and its files.
export interface SkillEntry {
  description: string;
  // locked: the control plane itself runs or quotes the skill, so it is always the shipped files.
  source: { kind: "git" | "upload" | "shipped"; url?: string; ref?: string; path?: string; commit?: string; by?: string; locked?: boolean };
  files: Record<string, string>;
}

// A git repository as a skill's import sees it; ref is the one paths were listed at.
export interface SkillSource {
  branches: string[];
  tags: string[];
  default_branch: string | null;
  ref: string | null;
  paths: string[];
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

// An engine the router's classifier can name, with the models it offers: none when its server decides them.
export interface ClassifierEngine {
  engine: string;
  models: string[];
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
  // The programs a harness image has, which a profile's bash allowlist is picked from; no image: the default.
  // A 422 says why they cannot be listed.
  configImagePrograms: (image?: string) =>
    call<{ image: string; programs: string[] }>(`/v1/config/images/programs${query({ image })}`),
  configClassifiers: () => call<{ engines?: ClassifierEngine[] }>("/v1/config/classifiers").then((r) => r?.engines ?? []),
  // Read a skill from a git repository or from its files, to look at before it is staged. Saves nothing.
  readSkill: (from: { url?: string; ref?: string; path?: string; files?: Record<string, string> }) =>
    call<{ name: string; skill: SkillEntry }>("/v1/config/skills/read", { method: "POST", body: JSON.stringify(from) }),
  // What a git repository offers an import: its branches and tags, and the directories that hold a SKILL.md at
  // ref (the default branch when empty). Saves nothing; a 422 says why the repository cannot be listed.
  skillSource: (url: string, ref?: string) => call<SkillSource>(`/v1/config/skills/source${query({ url, ref })}`),
  config: (kind: ConfigKind) => call<ConfigVersion>(`/v1/config/${kind}`),
  configVersions: (kind: ConfigKind) =>
    call<{ versions: ConfigVersion[] }>(`/v1/config/${kind}/versions`).then((r) => r.versions),
  saveConfig: (kind: ConfigKind, document: unknown, note: string) =>
    call<ConfigVersion>(`/v1/config/${kind}`, { method: "PUT", body: JSON.stringify({ document, note: note || null }) }),
  rollbackConfig: (kind: ConfigKind, version: number) =>
    call<ConfigVersion>(`/v1/config/${kind}/rollback`, { method: "POST", body: JSON.stringify({ version }) }),
};
