import { Navigate, useLocation } from "react-router-dom";
import { IconChartBar } from "@tabler/icons-react";
import { PerformancePage } from "./PerformancePage";
import type { Feature } from "../types";

// What the work costs, and how well it runs: named Performance since 2026-11-24. The address it had as Costs
// still leads to it, with whatever filters the link carried.
function CostsMoved() {
  const { search } = useLocation();
  return <Navigate to={`/performance${search}`} replace />;
}

export const feature: Feature = {
  routes: [
    { path: "/performance", element: <PerformancePage /> },
    // The page's address until it was named Performance: old links keep their filters.
    { path: "/costs", element: <CostsMoved /> },
  ],
  nav: [{ section: "Operate", label: "Performance", to: "/performance", icon: IconChartBar }],
};
