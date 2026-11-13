import { IconTarget } from "@tabler/icons-react";
import { GoalHistory } from "./GoalHistory";
import { GoalPage } from "./GoalPage";
import { GoalsPage } from "./GoalsPage";
import { NewGoal } from "./NewGoal";
import type { Feature } from "../types";

export const feature: Feature = {
  routes: [
    { path: "/goals", element: <GoalsPage /> },
    { path: "/goals/new", element: <NewGoal /> },
    { path: "/goals/:goalId", element: <GoalPage /> },
  ],
  nav: [{ section: "Operate", label: "Goals", to: "/goals", icon: IconTarget, history: GoalHistory }],
};
