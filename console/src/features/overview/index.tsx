import { IconLayoutDashboard } from "@tabler/icons-react";
import { Overview } from "./Overview";
import type { Feature } from "../types";

export const feature: Feature = {
  routes: [{ path: "/", element: <Overview /> }],
  nav: [{ section: "Operate", label: "Overview", to: "/", icon: IconLayoutDashboard }],
};
