import { SettingsPage } from "./SettingsPage";
import type { Feature } from "../types";

export const feature: Feature = {
  routes: [{ path: "/settings", element: <SettingsPage /> }],
  nav: [],
};
