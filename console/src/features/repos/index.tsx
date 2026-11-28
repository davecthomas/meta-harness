import { IconGitBranch } from "@tabler/icons-react";
import { FailingCount } from "./RepoMarker";
import { ReposPage } from "./ReposPage";
import type { Feature } from "../types";

// Repos: how each repo's own checks and deploy stand, and the settings Muxxy keeps for it.
export const feature: Feature = {
  routes: [{ path: "/repos", element: <ReposPage /> }],
  nav: [{ section: "Operate", label: "Repos", to: "/repos", icon: IconGitBranch, last: true, badge: FailingCount }],
};
