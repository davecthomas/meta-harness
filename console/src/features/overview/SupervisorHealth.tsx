import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Anchor, List, Text } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";
import { api } from "../../api";
import type { SupervisorState } from "../tasks/api";

// The Needs you list is the supervisor's: what it found an operator to have. So the Overview says when the
// supervisor itself is not keeping up (off, late, or failing), and when a goal's sweep cannot finish, since
// either means the list below may be missing something. Nothing when both are well.

// What is wrong with the supervisor, as sentences; empty when nothing is.
export function supervisorTrouble(state: SupervisorState, now = Date.now()): string[] {
  const trouble: string[] = [];
  const pass = state.last_pass;
  if (state.every_seconds === 0) return ["The supervisor is turned off (MH_SUPERVISOR_SECONDS is 0): nothing checks that tasks are still moving."];
  if (pass?.error) trouble.push(`The supervisor's last pass failed: ${pass.error}`);
  const at = pass?.at ? Date.parse(pass.at) : NaN;
  const late = Math.max(300, state.every_seconds * 5) * 1000;
  if (!pass || Number.isNaN(at)) trouble.push("The supervisor has not finished a pass yet.");
  else if (now - at > late) trouble.push(`The supervisor last finished a pass ${Math.round((now - at) / 60000)} minutes ago. It should run every ${state.every_seconds} seconds.`);
  for (const error of pass?.errors ?? []) trouble.push(`It could not look at a task: ${error}`);
  return trouble;
}

export function SupervisorHealth() {
  const [state, setState] = useState<SupervisorState | null>(null);
  useEffect(() => {
    const load = () => api.supervisor().then(setState, () => setState(null));
    void load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, []);
  if (!state) return null;
  const trouble = supervisorTrouble(state);
  const sweeps = Object.entries(state.sweeps_failing ?? {});
  if (!trouble.length && !sweeps.length) return null;
  return (
    <Alert color="yellow" icon={<IconAlertTriangle size={18} />} mb="md" title="This list may be missing something" data-testid="supervisor-health">
      <List size="sm" spacing={2}>
        {trouble.map((line) => <List.Item key={line}>{line}</List.Item>)}
        {sweeps.map(([goalId, sweep]) => (
          <List.Item key={goalId}>
            The sweep of goal <Anchor component={Link} to={`/goals/${goalId}`} ff="monospace" size="sm">{goalId}</Anchor> could not finish: {sweep.errors.join("; ")}
          </List.Item>
        ))}
      </List>
      <Text size="xs" c="dimmed" mt={4}>The planner's log has the detail. A sweep that keeps failing means that goal takes in no issues and ends no tasks.</Text>
    </Alert>
  );
}
