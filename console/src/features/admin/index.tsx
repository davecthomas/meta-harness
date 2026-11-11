import { IconBrandGithub, IconCloud, IconFileText, IconHistory, IconPlayerStop, IconRocket, IconUsers } from "@tabler/icons-react";
import { UsersPage } from "./UsersPage";
import { AuditPage, CloudPage, DeploysPage, KillSwitchPage } from "./AdminPages";
import { LogsPage } from "./LogsPage";
import { ReposPage } from "./ReposPage";
import type { Feature } from "../types";

export const feature: Feature = {
  routes: [
    { path: "/admin/users", element: <UsersPage /> },
    { path: "/admin/repos", element: <ReposPage /> },
    { path: "/admin/kill-switch", element: <KillSwitchPage /> },
    { path: "/admin/deploys", element: <DeploysPage /> },
    { path: "/admin/logs", element: <LogsPage /> },
    { path: "/admin/audit", element: <AuditPage /> },
    { path: "/admin/cloud", element: <CloudPage /> },
  ],
  nav: [
    { section: "Admin", label: "Users", to: "/admin/users", icon: IconUsers, admin: true },
    { section: "Admin", label: "Repositories", to: "/admin/repos", icon: IconBrandGithub, admin: true },
    { section: "Admin", label: "Kill switch", to: "/admin/kill-switch", icon: IconPlayerStop, admin: true },
    { section: "Admin", label: "Deploys", to: "/admin/deploys", icon: IconRocket, admin: true },
    { section: "Admin", label: "Logs", to: "/admin/logs", icon: IconFileText, admin: true },
    { section: "Admin", label: "Audit", to: "/admin/audit", icon: IconHistory, admin: true },
    { section: "Admin", label: "Google Cloud", to: "/admin/cloud", icon: IconCloud, admin: true },
  ],
};
