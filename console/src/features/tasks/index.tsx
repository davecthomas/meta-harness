import { IconListCheck } from "@tabler/icons-react";
import { TaskPage } from "./TaskPage";
import { TasksPage } from "./TasksPage";
import type { Feature } from "../types";

export const feature: Feature = {
  routes: [
    { path: "/tasks", element: <TasksPage /> },
    { path: "/tasks/:taskId", element: <TaskPage /> },
  ],
  nav: [{ section: "Operate", label: "Tasks", to: "/tasks", icon: IconListCheck }],
};
