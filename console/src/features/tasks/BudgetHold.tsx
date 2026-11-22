import { useEffect, useState } from "react";
import { Alert, Anchor, Button, Group, NumberInput, Stack, Text } from "@mantine/core";
import { IconCoin } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { api, type Task } from "../../api";
import { ConfirmButton } from "../../components/ConfirmButton";
import { RaiseGoalBudget } from "../goals/budget";
import type { BudgetResume } from "../goals/api";
import { moneyText } from "../../money";
import { useBudgetAnswer } from "./budgetAnswer";

const usd = (value: string | number) => `$${Number(value).toFixed(2)}`;

// A running task held for budget says so at the top of its page and asks: raise the limit and carry on, or
// end the task. A hold for the goal's budget has a field for the goal's new budget, which raises it and tells
// the task to look again; the task also looks again by itself within five minutes, or at once with Look again now.
// Once an answer is accepted, a line says what was sent until the task's workflow has taken it
// (useBudgetAnswer): the task then waits no more, or the alert comes back with the reason the workflow wrote.
export function BudgetHold({ task, onAnswered }: { task: Task; onAnswered: (task: Task) => void }) {
  const current = Number(task.max_usd ?? 0);
  // What the hold asks for, when the service could say: enough for the run that cannot start.
  const asked = task.budget_request?.for === "task" && task.budget_request.to_usd ? task.budget_request : null;
  const [limit, setLimit] = useState<number | string>(asked ? Number(asked.to_usd) : Math.ceil(current * 1.5) || 10);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reread = () => void api.task(task.id).then(onAnswered, () => undefined);
  const { sent, send } = useBudgetAnswer(task, reread);
  // A raised limit, or a new request, starts the field again from what the hold now asks for.
  const askedTo = asked?.to_usd;
  useEffect(() => {
    setLimit(askedTo ? Number(askedTo) : Math.ceil(current * 1.5) || 10);
  }, [current, askedTo]);
  if (task.status !== "running" || task.waiting_on !== "budget") return null;
  const forGoal = (task.status_reason ?? "").includes("goal ") && task.goal_id !== null;

  if (sent) {
    return (
      <Group gap="xs" mb="md" wrap="nowrap" role="status" data-testid="budget-answer-sent">
        <IconCoin size={18} />
        <Text size="sm">{sent.says}</Text>
      </Group>
    );
  }

  async function answer(action: "raise" | "end", max_usd?: number) {
    setBusy(true);
    setError(null);
    try {
      const answered = await api.decideBudget(task.id, action, max_usd);
      onAnswered(answered);
      send(
        action === "end"
          ? "Sent: end the task. It ends at its next step."
          : max_usd
            ? `Limit raised to ${usd(max_usd)}. The task continues at its next step.`
            : "Sent: look again at the goal's budget. The task continues at its next step if the budget allows it.",
        answered.updated_at,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  // The goal's budget is raised. The goal tells its held tasks to look again, but one it could not tell
  // (not in tasks_told) would wait for its own recheck, with no answer to write the hold again: this task is
  // then asked to look again itself, and the alert stays if that fails.
  async function goalRaised(resumed: BudgetResume) {
    const says = `The goal's budget raised to ${usd(resumed.to)}. The task continues at its next step.`;
    if (resumed.tasks_told.includes(task.id)) {
      send(says, task.updated_at);
      reread();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const answered = await api.decideBudget(task.id, "raise");
      onAnswered(answered);
      send(says, answered.updated_at);
    } catch (err) {
      setError(`The goal's budget was raised to ${usd(resumed.to)}, but the task could not be told to look again: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Alert
      color="orange"
      title={asked ? `Waiting on you: this task asks for $${Number(asked.add_usd).toFixed(2)} more` : "Waiting on you: this task is out of budget"}
      icon={<IconCoin size={18} />}
      mb="md"
      role="alert"
    >
      <Stack gap="sm">
        <Text size="sm">{moneyText(task.status_reason)}</Text>
        {forGoal ? (
          <Stack gap="sm">
            {/* Raises the goal's budget here, which tells this task to look again. */}
            <RaiseGoalBudget
              goalId={task.goal_id!}
              onResumed={(resumed) => void goalRaised(resumed)}
            />
            <Group gap="sm" align="center">
              <Anchor component={Link} to={`/goals/${task.goal_id}`} size="sm">The goal's page</Anchor>
              <Button size="xs" variant="light" loading={busy} onClick={() => void answer("raise")}>Look again now</Button>
              <ConfirmButton
                size="xs"
                variant="light"
                color="red"
                disabled={busy}
                label="End the task"
                title="End this task?"
                says="It ends at its next step and its run stops. A pull request it opened stays open. Retry on the task's page starts it again."
                confirm="End the task"
                cancel="Keep it waiting"
                onConfirm={() => answer("end")}
              />
            </Group>
          </Stack>
        ) : asked?.over_ceiling ? (
          // The run needs more than one task may spend, so a raise would be refused: say so, with what can be done.
          <Stack gap="sm" data-testid="over-ceiling">
            <Text size="sm">
              Its limit would have to be ${Number(asked.to_usd).toFixed(2)}, more than the ${Number(asked.ceiling_usd).toFixed(2)} one task may spend, so it cannot be raised.
              Ended, it stops with its pull request open, to retry or start over.
            </Text>
            <Group gap="sm" align="center">
              <ConfirmButton
                variant="light"
                color="red"
                disabled={busy}
                label="End the task"
                title="End this task?"
                says="It ends at its next step and its run stops. A pull request it opened stays open. Retry on the task's page starts it again."
                confirm="End the task"
                cancel="Keep it waiting"
                onConfirm={() => answer("end")}
              />
              <Anchor component={Link} to="/config?kind=task_policy" size="sm">Change what one task may spend</Anchor>
            </Group>
          </Stack>
        ) : (
          <Group gap="sm" align="flex-end">
            {asked && (
              // The request itself, granted in one click: enough for the run that cannot start, in whole dollars.
              <Button loading={busy} data-testid="grant-request" onClick={() => void answer("raise", Number(asked.to_usd))}>
                Grant ${Number(asked.add_usd).toFixed(2)}: its limit becomes ${Number(asked.to_usd).toFixed(2)}
              </Button>
            )}
            <NumberInput
              label={asked ? "Or set another limit" : "New spend limit"}
              description={`Now $${current.toFixed(2)}, for all the task's runs`}
              prefix="$"
              min={0}
              decimalScale={2}
              value={limit}
              onChange={setLimit}
              w={220}
            />
            <Button variant={asked ? "default" : "filled"} loading={busy} disabled={!(Number(limit) > current)} onClick={() => void answer("raise", Number(limit))}>
              Raise the limit and carry on
            </Button>
            <ConfirmButton
                variant="light"
                color="red"
                disabled={busy}
                label="End the task"
                title="End this task?"
                says="It ends at its next step and its run stops. A pull request it opened stays open. Retry on the task's page starts it again."
                confirm="End the task"
                cancel="Keep it waiting"
                onConfirm={() => answer("end")}
              />
          </Group>
        )}
        {error && <Text size="sm" c="red">{error}</Text>}
        <Text size="sm" c="dimmed">With no answer, it waits up to a day, keeping its place among the running tasks, then ends.</Text>
      </Stack>
    </Alert>
  );
}
