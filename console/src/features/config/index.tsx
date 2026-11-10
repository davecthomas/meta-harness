import { IconAdjustments } from "@tabler/icons-react";
import { ConfigPage } from "./ConfigPage";
import type { Feature } from "../types";

export const feature: Feature = {
  routes: [{ path: "/config", element: <ConfigPage /> }],
  nav: [{ section: "Admin", label: "Configuration", to: "/config", icon: IconAdjustments, admin: true }],
};
