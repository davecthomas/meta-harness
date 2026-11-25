import { PlanPage } from "./PlanPage";
import type { Feature } from "../types";

// The plan has a page of its own: what it asks, a few figures, what changed, its tasks and its versions.
export const feature: Feature = {
  routes: [{ path: "/goals/:goalId/plan", element: <PlanPage /> }],
  nav: [],
};
