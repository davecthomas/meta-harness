import { IconChartBar } from "@tabler/icons-react";
import { PerformancePage } from "./PerformancePage";
import type { Feature } from "../types";

export const feature: Feature = {
  routes: [{ path: "/performance", element: <PerformancePage /> }],
  nav: [{ section: "Operate", label: "Performance", to: "/performance", icon: IconChartBar }],
};
