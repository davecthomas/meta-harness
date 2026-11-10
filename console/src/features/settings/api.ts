import { call } from "../../client";

// The signed-in user's own settings, from GET /v1/me/settings.
export interface UserSettings {
  planner: { repos: string[]; modality: "fix" | "feature" | "new-system"; latitude: number | null; max_usd: string | null; auto_merge: boolean };
  appearance: "light" | "dark" | "system";
  notifications: boolean;
}

// The settings feature's calls; src/api.ts spreads them into `api`.
export const settingsApi = {
  settings: () => call<UserSettings>("/v1/me/settings"),
  saveSettings: (settings: UserSettings) => call<UserSettings>("/v1/me/settings", { method: "PUT", body: JSON.stringify(settings) }),
};
