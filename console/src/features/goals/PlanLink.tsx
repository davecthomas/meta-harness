import { useEffect, useState } from "react";
import { Badge, Button, Group, Paper, Text } from "@mantine/core";
import { IconArrowRight } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { api, type GoalPlan as Plan } from "../../api";
import { PLAN_CHANGED } from "./needActions";

// The goal's plan, as one line on the goal's page: what it asks now, and the way to its own page, where it is
// read, changed and approved. The plan lives in one place, and every other page links there.
// How often a planning goal's plan is read again while its page is open: the planner and its reviewer move it
// on without the operator, and the page must say where it stands now.
export const PLAN_READ_MS = 5000;

// The goal's plan as the planner serves it, with the one thing it asks now (`decision`), read again while the
// planner works and at once when the plan changes from this page. Null until read, and for a goal not planning.
export function usePlan(goalId: string, planning = true): Plan | null {
  const [plan, setPlan] = useState<Plan | null>(null);
  useEffect(() => {
    if (!planning) {
      setPlan(null);
      return;
    }
    const read = () => void api.goalPlan(goalId).then(setPlan, () => setPlan(null));
    read();
    const timer = window.setInterval(read, PLAN_READ_MS);
    window.addEventListener(PLAN_CHANGED, read);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener(PLAN_CHANGED, read);
    };
  }, [goalId, planning]);
  return plan;
}

export function PlanLink({ goalId }: { goalId: string }) {
  const plan = usePlan(goalId);
  const decision = plan?.decision;
  const first = decision?.buttons[0];
  // Named for the decision when there is one, so the button says what pressing through will do.
  const label = first?.action === "approve_plan" ? "Review and approve the plan" : "Open the plan";
  return (
    <Paper withBorder p="md" radius="md" mb="md" data-testid="plan-link">
      <Group justify="space-between" wrap="wrap" gap="sm">
        <Group gap="sm" style={{ flex: "1 1 280px" }}>
          <Badge variant="light" tt="none" radius="sm">The plan{plan?.plan ? `, version ${plan.plan.version}` : ""}</Badge>
          <Text size="sm">{decision?.says ?? "Reading the plan."}</Text>
        </Group>
        <Button component={Link} to={`/goals/${goalId}/plan`} size="xs" color={first?.action === "approve_plan" ? "teal" : undefined} variant={first?.action === "approve_plan" ? "filled" : "default"} rightSection={<IconArrowRight size={14} />}>
          {label}
        </Button>
      </Group>
    </Paper>
  );
}
