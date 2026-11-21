import type { TaskStanding } from "../../api";

// A task's standing in one line, for a list: why it is where it is, and for a person what to do. A task's own
// page says it in the Task Shepherd's panel (ShepherdPanel.tsx).
export function standingLine(standing: TaskStanding): string {
  const reason = standing.reason.charAt(0).toUpperCase() + standing.reason.slice(1);
  return standing.ask ? `${reason}. ${standing.ask}` : `${reason}.`;
}
