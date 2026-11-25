import { GitHubConnected } from "./GitHubConnect";
import type { Feature } from "../types";

// An operator's own GitHub connection: the page GitHub returns to. Its section lives on the Settings page.
export const feature: Feature = {
  routes: [{ path: "/github/connected", element: <GitHubConnected /> }],
  nav: [],
};
