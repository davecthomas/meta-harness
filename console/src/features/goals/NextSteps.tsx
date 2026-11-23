import { useCallback, useEffect, useState } from "react";
import { Alert, Anchor, Badge, Button, Collapse, Group, Loader, Paper, Stack, Text } from "@mantine/core";
import { IconArrowRight, IconBulb } from "@tabler/icons-react";
import { Link, useNavigate } from "react-router-dom";
import { call } from "../../client";
import { VoiceMark } from "../../components/Voice";

// A goal's next steps as the planner serves them (planner/next_steps.py): what it would do next if it carried
// the work on, each with the statement of a new goal, and the goal started from it when one was.
export interface NextStep {
  id: string;
  title: string;
  why: string;
  statement: string;
  goal: { id: string; title: string; status: string } | null;
}

export interface NextStepsRead {
  steps: NextStep[];
  written_at: string | null;
  started: { id: string; title: string; status: string }[];
  // The goal this one was started from, when it came from a next step.
  continues: { id: string; title: string } | null;
  // The budget a goal started here is given.
  budget_usd: string;
  enabled: boolean;
  stale: boolean;
  why: string;
  writing: boolean;
}

// How often the page asks again while the steps are being written.
const AGAIN_MS = 3000;

function Step({ step, goalId, onStarted }: { step: NextStep; goalId: string; onStarted: (goalId: string) => void }) {
  const [shown, setShown] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setStarting(true);
    setError(null);
    try {
      const made = await call<{ goal_id: string }>(`/v1/goals/${goalId}/next-steps/${step.id}/goal`, { method: "POST" });
      onStarted(made.goal_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStarting(false);
    }
  }

  return (
    <Paper withBorder p="sm" radius="md" data-testid="next-step">
      <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
        <Stack gap={2} style={{ flex: "1 1 320px" }}>
          <Text fw={650}>{step.title}</Text>
          <Text size="sm">{step.why}</Text>
          <Anchor component="button" type="button" size="sm" style={{ alignSelf: "flex-start" }} aria-expanded={shown} onClick={() => setShown((now) => !now)}>
            {shown ? "Hide the goal it starts" : "Show the goal it starts"}
          </Anchor>
          <Collapse expanded={shown}>
            <Text size="sm" c="dimmed" style={{ whiteSpace: "pre-wrap" }} data-testid="next-step-statement">{step.statement}</Text>
          </Collapse>
        </Stack>
        {step.goal ? (
          <Group gap="xs" wrap="nowrap">
            <Badge variant="light" color="gray">{step.goal.status}</Badge>
            <Anchor component={Link} to={`/goals/${step.goal.id}`} size="sm">Open the goal started from this</Anchor>
          </Group>
        ) : (
          <Button size="sm" color="teal" loading={starting} rightSection={<IconArrowRight size={16} />} onClick={() => void start()}>
            Start a goal from this
          </Button>
        )}
      </Group>
      {error && <Alert color="red" variant="light" mt="xs" p="xs" role="alert">{error}</Alert>}
    </Paper>
  );
}

// What the planner would do next, at the head of a goal's page under its update. ended: the goal is met or
// canceled, so its next steps are asked for, and written, as the page opens. A running goal shows what is
// stored and writes none until the operator asks: its work is not done, and each writing is a model call.
// watch: changes when the goal does, so the steps are read again.
// planning: the goal's plan is not approved yet, so there is nothing to carry on from: only where the goal
// came from is said.
export function NextSteps({ goalId, ended, planning = false, watch = "" }: { goalId: string; ended: boolean; planning?: boolean; watch?: string }) {
  const [read, setRead] = useState<NextStepsRead | null>(null);
  const [asked, setAsked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const writes = ended || asked;

  const look = useCallback(
    () => call<NextStepsRead>(`/v1/goals/${goalId}/next-steps${writes ? "" : "?write=false"}`),
    [goalId, writes],
  );

  useEffect(() => {
    let current = true;
    let again: ReturnType<typeof setTimeout> | undefined;
    const load = () =>
      look().then(
        (found) => {
          if (!current) return;
          setRead(found);
          setError(null);
          if (found.writing) again = setTimeout(load, AGAIN_MS);
        },
        (err) => {
          if (!current) return;
          // A running goal's page asked for nothing: a read that fails there is not worth a red box.
          if (writes) setError(err instanceof Error ? err.message : String(err));
        },
      );
    void load();
    return () => {
      current = false;
      clearTimeout(again);
    };
  }, [look, writes, watch]);

  if (!read && !error) return null;
  // An answer that is not the steps (a page over an API that does not serve them) shows nothing.
  if (read && !Array.isArray(read.steps)) return null;
  if (read && !read.enabled && !read.continues && !read.steps.length) return null;
  const steps = read?.steps ?? [];
  const writing = !!read?.writing;
  const written = !!read?.written_at;
  const continues = read?.continues && (
    <Text size="sm" data-testid="next-steps-continues">
      This goal was started from a next step of <Anchor component={Link} to={`/goals/${read.continues.id}`}>{read.continues.title}</Anchor>.
    </Text>
  );
  if (planning) return continues ? <Paper withBorder p="sm" mb="md" data-testid="next-steps">{continues}</Paper> : null;
  // A running goal that has been given no steps keeps to one line: the offer, and the button that asks.
  if (!ended && !steps.length && !writing && !error) {
    return (
      <Paper withBorder p="sm" mb="md" data-testid="next-steps">
        <Group justify="space-between" wrap="wrap" gap="sm">
          <Stack gap={2}>
            <Text size="sm"><Text span fw={650}>Next steps. </Text>{written ? "I found nothing in this goal's records that calls for more work yet." : "I suggest what to do after this goal by myself once it ends."}</Text>
            {continues}
          </Stack>
          {read?.enabled && (
            <Button size="xs" variant="default" color="teal" leftSection={<IconBulb size={14} />} onClick={() => setAsked(true)}>
              {written ? "Suggest again" : "Suggest next steps now"}
            </Button>
          )}
        </Group>
      </Paper>
    );
  }

  return (
    <Paper withBorder p="lg" mb="md" radius="md" data-testid="next-steps" style={{ borderLeft: "5px solid var(--mantine-color-teal-6)" }}>
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Group gap="sm" wrap="nowrap" align="flex-start">
          <VoiceMark who="planner" size={38} />
          <Stack gap={2}>
            <Text size="xs" tt="uppercase" fw={700} c="teal" style={{ letterSpacing: 0.4 }}>Next steps</Text>
            <Text size="lg" fw={650} lh={1.3}>
              {steps.length ? "If I carried this work on, here is what I would do next." : "What I would do next if I carried this work on."}
            </Text>
          </Stack>
        </Group>
        {!ended && !asked && read?.enabled && (
          <Button size="xs" variant="default" color="teal" style={{ flexShrink: 0 }} leftSection={<IconBulb size={14} />} onClick={() => setAsked(true)}>
            Suggest again
          </Button>
        )}
      </Group>
      <Stack gap="sm" pl={50} mt="xs">
        {continues}
        {writing && (
          <Group gap="xs" role="status" data-testid="next-steps-writing">
            <Loader size="xs" />
            <Text size="sm">I'm reading this goal's records to work out what comes next, which takes a few seconds.</Text>
          </Group>
        )}
        {error && <Alert color="red" variant="light" p="xs" role="alert">{error}</Alert>}
        {read?.stale && read.why && <Text size="sm" c="dimmed">These are older than the goal: {read.why}.</Text>}
        {steps.map((step) => <Step key={step.id} step={step} goalId={goalId} onStarted={(id) => navigate(`/goals/${id}`)} />)}
        {steps.length > 0 && (
          <Text size="sm" c="dimmed" data-testid="next-steps-says">
            A goal started here takes this goal's repos and settings, with a budget of ${Number(read?.budget_usd ?? 0).toFixed(2)}. It is planned first: nothing runs until you approve its plan.
          </Text>
        )}
        {!steps.length && !writing && written && !error && (
          <Text size="sm" data-testid="next-steps-none">I found nothing in this goal's records that calls for more work.</Text>
        )}
      </Stack>
    </Paper>
  );
}
