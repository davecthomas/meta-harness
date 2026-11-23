import { Navigate } from "react-router-dom";
import { IconChartBar } from "@tabler/icons-react";
import { PerformancePage } from "./PerformancePage";
import type { Feature } from "../types";

// The page is what Octofusion costs, so it is named Costs. The address it had still leads to it.
export const feature: Feature = {
  routes: [
    { path: "/costs", element: <PerformancePage /> },
    { path: "/performance", element: <Navigate to="/costs" replace /> },
  ],
  nav: [{ section: "Operate", label: "Costs", to: "/costs", icon: IconChartBar }],
};
