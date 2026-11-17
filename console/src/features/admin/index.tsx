import { Navigate } from "react-router-dom";
import { IconCloud, IconFileText, IconHistory, IconRocket, IconUsers } from "@tabler/icons-react";
import { UsersPage } from "./UsersPage";
import { AuditPage, CloudPage, DeploysPage } from "./AdminPages";
import { LogsPage } from "./LogsPage";
import type { Feature } from "../types";

export const feature: Feature = {
  routes: [
    { path: "/admin/users", element: <UsersPage /> },
    // Each thing has one place. Repositories are under Configuration, and the kill switch is in the header of
    // every page: the addresses these once had lead there.
    { path: "/admin/repos", element: <Navigate to="/config?kind=repos" replace /> },
    { path: "/admin/kill-switch", element: <Navigate to="/" replace /> },
    { path: "/admin/deploys", element: <DeploysPage /> },
    { path: "/admin/logs", element: <LogsPage /> },
    { path: "/admin/audit", element: <AuditPage /> },
    { path: "/admin/cloud", element: <CloudPage /> },
  ],
  nav: [
    { section: "Admin", label: "Users", to: "/admin/users", icon: IconUsers, admin: true },
    { section: "Admin", label: "Deploys", to: "/admin/deploys", icon: IconRocket, admin: true },
    { section: "Admin", label: "Logs", to: "/admin/logs", icon: IconFileText, admin: true },
    { section: "Admin", label: "Audit", to: "/admin/audit", icon: IconHistory, admin: true },
    { section: "Admin", label: "Google Cloud", to: "/admin/cloud", icon: IconCloud, admin: true },
  ],
};
