import { type ReactNode, useEffect, useState } from "react";
import { Alert, Badge, Button, Group, Modal, NumberInput, Stack, Text } from "@mantine/core";
import { IconCoin, IconPlayerPlay } from "@tabler/icons-react";
import { api, type Goal } from "../../api";
import type { BudgetResume } from "./api";
import { BudgetEdit, type FieldProps } from "./GoalFields";
import { InlineEdit, InlineSwitch, refusal } from "./InlineEdit";

// A goal's budget in the console: where the goal stands against it, the two fields that say what it does when
// it is reached, the alert for a goal past it, and the one-step Resume that raises it.

type Standing = "ok" | "over" | "blocked";
type Budgeted = Pick<Goal, "status" | "max_usd" | "budget_mode" | "budget_block_at" | "budget_state" | "spent_usd">;

const usd = (value: string | number | null | undefined) => `$${Number(value ?? 0).toFixed(2)}`;

// Where the goal stands against its budget. spent: what the page has added up from the goal's runs, which is
// newer than the planner's last look; without it, the planner's own answer.
export function budgetStanding(goal: Budgeted, spent?: number): Standing {
  if (!goal.max_usd) return "ok";
  if (goal.status === "budget_exhausted" || goal.budget_state === "blocked") return "blocked";
  if (spent === undefined) return goal.budget_state ?? "ok";
  if (goal.budget_block_at && spent >= Number(goal.budget_block_at)) return "blocked";
  return spent >= Number(goal.max_usd) || goal.budget_state === "over" ? "over" : "ok";
}

// The words after the spend, for a goal past its budget: "over budget, stops at $11.00".
export function budgetWords(goal: Budgeted, spent?: number): string | null {
  const standing = budgetStanding(goal, spent);
  if (standing === "ok") return null;
  if (standing === "blocked") return "stopped by its budget";
  return goal.budget_block_at ? `over budget, stops at ${usd(goal.budget_block_at)}` : "over budget, and it keeps going";
}

export const budgetColor = (standing: Standing) => (standing === "blocked" ? "red" : standing === "over" ? "orange" : "blue");

// In a list: nothing for a goal within its budget, else how it stands.
export function BudgetMark({ goal }: { goal: Budgeted }) {
  const standing = budgetStanding(goal);
  if (standing === "ok") return null;
  return <Badge color={budgetColor(standing)} variant="light" tt="none">{standing === "blocked" ? "stopped by budget" : "over budget"}</Badge>;
}

export function BudgetModeEdit({ goal, readOnly, save }: FieldProps) {
  return (
    <InlineSwitch
      label="Stop when the budget is reached"
      checked={goal.budget_mode !== "warn"}
      onText="Stop"
      offText="Warn and keep going"
      readOnly={readOnly}
      save={(stop) => save({ budget_mode: stop ? "block" : "warn" })}
    />
  );
}

export function OverageEdit({ goal, readOnly, save }: FieldProps) {
  const percent = goal.budget_overage_percent ?? 10;
  return (
    <InlineEdit<number | string>
      label="allowed overage"
      readOnly={readOnly}
      value={percent}
      display={
        <Text size="sm">
          {percent}% over{goal.budget_block_at ? `, stops at ${usd(goal.budget_block_at)}` : ""}
        </Text>
      }
      same={(a, b) => String(a) === String(b)}
      check={(v) => (v !== "" && Number.isInteger(Number(v)) && Number(v) >= 0 ? null : "The overage is a whole percent, 0 or more.")}
      save={(v) => save({ budget_overage_percent: Number(v) })}
      input={({ value, onChange, invalid }) => (
        <NumberInput
          aria-label="Allowed overage"
          description="How far past its budget the goal may go before it stops. 0 stops at the budget exactly."
          suffix="%"
          min={0}
          allowDecimal={false}
          value={value}
          error={invalid}
          onChange={onChange}
        />
      )}
    />
  );
}

// The goal page's Budget row: the amount, and for a goal with a budget what happens when it is reached and, for
// one that stops, how far past it the goal may go. One setting, so one row.
export function BudgetRow(props: FieldProps) {
  const { goal } = props;
  if (!goal.max_usd) return <BudgetEdit {...props} />;
  const part = (label: string, field: ReactNode) => (
    <Group gap={6} wrap="nowrap" align="center">
      <Text size="xs" c="dimmed">{label}</Text>
      {field}
    </Group>
  );
  return (
    <Group gap="md" wrap="wrap" align="center" data-testid="budget-row">
      <BudgetEdit {...props} />
      <Text size="sm" c="dimmed">·</Text>
      {part("When reached", <BudgetModeEdit {...props} />)}
      {goal.budget_mode !== "warn" && (
        <>
          <Text size="sm" c="dimmed">·</Text>
          {part("Overage", <OverageEdit {...props} />)}
        </>
      )}
    </Group>
  );
}

// Resume: raise the goal's budget so it can go on. It shows the budget a resume would set, which the operator
// may change before confirming. Given only a goal's id (a task's page, the Overview), it reads the goal when opened.
export function ResumeBudget(props: {
  goalId: string;
  goal?: Goal;
  onResumed?: (answer: BudgetResume) => void;
  size?: "xs" | "sm";
  variant?: string;
}) {
  const [opened, setOpened] = useState(false);
  const [goal, setGoal] = useState<Goal | null>(props.goal ?? null);
  const [to, setTo] = useState<number | string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const from = Number(goal?.max_usd ?? 0);
  // What the planner worked out; before its first look at a goal past its budget, half as much again as is spent.
  const suggested = (g: Goal) => Number(g.budget_resume_to ?? Math.ceil(Math.max(Number(g.spent_usd ?? 0), Number(g.max_usd ?? 0)) * 1.5));

  async function open() {
    setOpened(true);
    setError(null);
    try {
      const found = props.goal ?? (await api.goal(props.goalId));
      setGoal(found);
      setTo(suggested(found));
    } catch (err) {
      setError(refusal(err));
    }
  }

  async function confirm() {
    if (!goal) return;
    setBusy(true);
    setError(null);
    try {
      // Unchanged: the planner works the amount out again, and records how. Changed: the operator's amount.
      const own = goal.budget_resume_to === null || goal.budget_resume_to === undefined || Number(to) !== Number(goal.budget_resume_to);
      const answer = await api.resumeBudget(props.goalId, own ? Number(to) - from : undefined);
      setOpened(false);
      props.onResumed?.(answer);
    } catch (err) {
      setError(refusal(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button size={props.size} variant={props.variant} leftSection={<IconPlayerPlay size={16} />} onClick={() => void open()}>
        Resume
      </Button>
      <Modal opened={opened} onClose={() => setOpened(false)} title="Raise the budget and carry on">
        <Stack gap="md">
          {goal && (
            <>
              <Text size="sm">
                The goal has spent {usd(goal.spent_usd)} of its {usd(from)} budget. Raising the budget lets its tasks start and go on.
              </Text>
              <NumberInput
                label={`Raise the budget from ${usd(from)} to`}
                description="What it has spent, plus what its issues not yet done are likely to cost. Change it if you like."
                prefix="$"
                min={0}
                decimalScale={2}
                value={to}
                onChange={setTo}
              />
            </>
          )}
          {error && <Text size="sm" c="red" role="alert">{error}</Text>}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setOpened(false)}>Cancel</Button>
            <Button loading={busy} disabled={!goal || !(Number(to) > from)} onClick={() => void confirm()}>
              Raise to {usd(to || 0)} and carry on
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}

// On a task's page, where the task waits for its goal's budget: the goal's budget and spend, a field holding the
// budget a resume would set, and one button that raises it. No dialog: the task's own limit is raised the same way.
export function RaiseGoalBudget(props: { goalId: string; onResumed?: (answer: BudgetResume) => void }) {
  const [goal, setGoal] = useState<Goal | null>(null);
  const [to, setTo] = useState<number | string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.goal(props.goalId).then(
      (found) => {
        setGoal(found);
        setTo(Number(found.budget_resume_to ?? Math.ceil(Math.max(Number(found.spent_usd ?? 0), Number(found.max_usd ?? 0)) * 1.5)));
      },
      (err) => setError(refusal(err)),
    );
  }, [props.goalId]);
  if (!goal) return error ? <Text size="sm" c="red">{error}</Text> : null;
  const from = Number(goal.max_usd ?? 0);

  async function raise() {
    setBusy(true);
    setError(null);
    try {
      // Unchanged: the planner works the amount out again, and records how. Changed: the operator's amount.
      const own = goal!.budget_resume_to == null || Number(to) !== Number(goal!.budget_resume_to);
      props.onResumed?.(await api.resumeBudget(props.goalId, own ? Number(to) - from : undefined));
    } catch (err) {
      setError(refusal(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack gap={4}>
      <Group gap="sm" align="flex-end">
        <NumberInput
          label="New goal budget"
          description={`Now ${usd(from)}, and the goal has spent ${usd(goal.spent_usd)}${goal.budget_block_at ? `; it stops at ${usd(goal.budget_block_at)}` : ""}`}
          prefix="$"
          min={0}
          decimalScale={2}
          value={to}
          onChange={setTo}
          w={300}
        />
        <Button loading={busy} disabled={!(Number(to) > from)} onClick={() => void raise()}>
          Raise the goal's budget and carry on
        </Button>
      </Group>
      {error && <Text size="sm" c="red" role="alert">{error}</Text>}
    </Stack>
  );
}

// On the goal's page: a goal past its budget says so, and offers Resume unless its budget only warns.
export function BudgetAlert({ goal, spent, onResumed }: { goal: Goal; spent?: number; onResumed: () => void }) {
  const standing = budgetStanding(goal, spent);
  if (standing === "ok") return null;
  const shown = usd(spent ?? goal.spent_usd);
  const warns = goal.budget_mode === "warn";
  return (
    <Alert
      color={budgetColor(standing)}
      title={standing === "blocked" ? "Stopped by its budget" : "Over budget"}
      icon={<IconCoin size={18} />}
      mb="md"
      role="alert"
      data-testid="budget-alert"
    >
      <Stack gap="sm" align="flex-start">
        <Text size="sm">
          {shown} spent of {usd(goal.max_usd)}: {budgetWords(goal, spent)}.{" "}
          {standing === "blocked"
            ? "None of its tasks starts, and its running tasks wait before their next run."
            : warns
              ? "Its budget only warns, so nothing is held."
              : "It runs on until it reaches that."}
        </Text>
        {!warns && <ResumeBudget goalId={goal.id} goal={goal} onResumed={onResumed} size="xs" />}
      </Stack>
    </Alert>
  );
}
