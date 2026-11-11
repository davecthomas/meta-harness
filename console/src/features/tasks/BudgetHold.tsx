import { useState } from "react";
import { Alert, Anchor, Button, Group, NumberInput, Stack, Text } from "@mantine/core";
import { IconCoin } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { api, type Task } from "../../api";
import { RaiseGoalBudget } from "../goals/budget";
import { moneyText } from "../../money";

// A running task held for budget says so at the top of its page and asks: raise the limit and carry on, or
// end the task. A hold for the goal's budget has a field for the goal's new budget, which raises it and tells
// the task to look again; the task also looks again by itself within five minutes, or at once with Look again now.
export function BudgetHold({ task, onAnswered }: { task: Task; onAnswered: (task: Task) => void }) {
  const current = Number(task.max_usd ?? 0);
  const [limit, setLimit] = useState<number | string>(Math.ceil(current * 1.5) || 10);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (task.status !== "running" || task.waiting_on !== "budget") return null;
  const forGoal = (task.status_reason ?? "").includes("goal ") && task.goal_id !== null;

  async function answer(action: "raise" | "end", max_usd?: number) {
    setBusy(true);
    setError(null);
    try {
      onAnswered(await api.decideBudget(task.id, action, max_usd));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Alert color="orange" title="Waiting on you: this task is out of budget" icon={<IconCoin size={18} />} mb="md" role="alert">
      <Stack gap="sm">
        <Text size="sm">{moneyText(task.status_reason)}</Text>
        {forGoal ? (
          <Stack gap="sm">
            {/* Raises the goal's budget here, which tells this task to look again. */}
            <RaiseGoalBudget goalId={task.goal_id!} onResumed={() => void api.task(task.id).then(onAnswered, () => undefined)} />
            <Group gap="sm" align="center">
              <Anchor component={Link} to={`/goals/${task.goal_id}`} size="sm">The goal's page</Anchor>
              <Button size="xs" variant="light" loading={busy} onClick={() => void answer("raise")}>Look again now</Button>
              <Button size="xs" variant="light" color="red" loading={busy} onClick={() => void answer("end")}>End the task</Button>
            </Group>
          </Stack>
        ) : (
          <Group gap="sm" align="flex-end">
            <NumberInput
              label="New spend limit"
              description={`Now $${current.toFixed(2)}, for all the task's runs`}
              prefix="$"
              min={0}
              decimalScale={2}
              value={limit}
              onChange={setLimit}
              w={220}
            />
            <Button loading={busy} disabled={!(Number(limit) > current)} onClick={() => void answer("raise", Number(limit))}>
              Raise the limit and carry on
            </Button>
            <Button variant="light" color="red" loading={busy} onClick={() => void answer("end")}>End the task</Button>
          </Group>
        )}
        {error && <Text size="sm" c="red">{error}</Text>}
        <Text size="xs" c="dimmed">It waits up to a day, holding its place among the running tasks, then ends as it would have.</Text>
      </Stack>
    </Alert>
  );
}
