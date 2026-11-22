import { useEffect, useState } from "react";
import { Anchor, Group, List, Stack, Text } from "@mantine/core";
import { IconPlayerPlay } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { ConfirmButton } from "../../components/ConfirmButton";
import { api } from "../../api";
import type { UnstickPlan, UnstickResult } from "./api";

const many = (n: number, one: string, more: string) => `${n} ${n === 1 ? one : more}`;

// One button for a goal with several tasks waiting on a person: each takes the step the Task Shepherd
// recommends for it (the planner's POST /v1/goals/{id}/unstick), so nobody presses Retry on each in turn. The
// dialog names every task and its step before anything runs, and the ones only a person can move. Shown only
// when at least one task has a step to take.
export function UnstickGoal({ goalId, watch, onChanged }: {
  goalId: string;
  // Changes when the goal's tasks do, so what the button would do is read again.
  watch: string;
  onChanged?: () => void;
}) {
  const [plan, setPlan] = useState<UnstickPlan | null>(null);
  const [result, setResult] = useState<UnstickResult | null>(null);

  useEffect(() => {
    let current = true;
    api.unstickPlan(goalId)
      .then((read) => { if (current) setPlan(read); })
      // The button is an extra: a goal's page still shows each task's own actions when this cannot be read.
      .catch(() => { if (current) setPlan(null); });
    return () => { current = false; };
  }, [goalId, watch]);

  const moves = plan?.moves ?? [];
  const left = plan?.left ?? [];
  const task = (id: string, name: string) => <Anchor component={Link} to={`/tasks/${id}`} size="sm">{name}</Anchor>;

  async function go() {
    const did = await api.unstick(goalId);
    setResult(did);
    onChanged?.();
  }

  if (!moves.length && !result) return null;
  const label = `Get ${many(moves.length, "task", "tasks")} moving`;
  return (
    <Stack gap={6} mb="sm" data-testid="unstick">
      {moves.length > 0 && (
        <Group gap="sm">
          <ConfirmButton
            size="compact-sm"
            variant="filled"
            leftSection={<IconPlayerPlay size={14} />}
            label={label}
            title={`${label}?`}
            confirm={label}
            says={
              <Stack gap="xs">
                <Text size="sm">Each takes the step recommended for it:</Text>
                <List size="sm" data-testid="unstick-steps">
                  {moves.map((m) => <List.Item key={m.task_id}>{m.name}: {m.does}</List.Item>)}
                </List>
                {left.length > 0 && (
                  <>
                    <Text size="sm">{many(left.length, "task needs", "tasks need")} you, and stay as they are:</Text>
                    <List size="sm" data-testid="unstick-left">
                      {left.map((l) => <List.Item key={l.task_id}>{l.name}: {l.why}</List.Item>)}
                    </List>
                  </>
                )}
                <Text size="sm" c="dimmed">A task that runs again can spend up to its own spend limit.</Text>
              </Stack>
            }
            onConfirm={go}
          />
        </Group>
      )}
      {result && (
        <Stack gap={2} data-testid="unstick-result" role="status">
          <Text size="sm">
            {result.done.length ? `${many(result.done.length, "task is", "tasks are")} moving again.` : "No task could be moved."}
          </Text>
          {result.failed.map((f) => <Text key={f.task_id} size="sm" c="red">{task(f.task_id, f.name)}: {f.why}</Text>)}
          {result.left.map((l) => <Text key={l.task_id} size="sm">Needs you: {task(l.task_id, l.name)}: {l.why}</Text>)}
        </Stack>
      )}
    </Stack>
  );
}
