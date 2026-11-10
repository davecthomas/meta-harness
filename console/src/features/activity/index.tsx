import { IconGridDots } from "@tabler/icons-react";
import { ActivityPage } from "./ActivityPage";
import type { Feature } from "../types";

export const feature: Feature = {
  routes: [{ path: "/activity", element: <ActivityPage /> }],
  nav: [{ section: "Operate", label: "Activity", to: "/activity", icon: IconGridDots }],
};
