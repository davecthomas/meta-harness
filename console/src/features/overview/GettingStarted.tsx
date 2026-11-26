import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Anchor, Button, Group, Paper, Stack, Text, ThemeIcon } from "@mantine/core";
import { IconCheck } from "@tabler/icons-react";
import { api } from "../../api";
import { useAdmin } from "../../components/Role";
import { SubTitle } from "../../components/ui";

// What a new deployment needs before any work runs, in order, each step ticked once it is done and linking to
// where it is done. It shows on the Overview until every step is done, then never again. Nothing else in the
// console says the order: a goal needs a repository, a repository needs the GitHub App, and an issue needs
// the goal's label.

interface Step {
  key: string;
  done: boolean;
  title: string;
  // What to do, and why it comes here in the order.
  says: string;
  to: string;
  button: string;
  // Only an admin can do it; anyone else is told to ask one.
  admin?: boolean;
}

export function GettingStarted() {
  const admin = useAdmin();
  const [steps, setSteps] = useState<Step[] | null>(null);
  useEffect(() => {
    // Read once, and only to decide whether to show: a failure leaves the checklist out, since each page it
    // would link to says for itself what is missing.
    Promise.all([api.repos(), api.trackers(), api.goals([], 0, 1), api.tasks({}, 0, 1)]).then(
      ([repos, trackers, goals, tasks]) => {
        const tracker = trackers.trackers.find((t) => t.scheme === trackers.default);
        const signedIn = !!tracker?.configured && tracker.signed_in;
        setSteps([
          {
            key: "repo",
            done: repos.length > 0,
            title: "Add a repository",
            says: "A goal changes code in a repository the console knows. Choose from the repositories the GitHub App is installed on.",
            to: "/config?kind=repos",
            button: "Add a repository",
            admin: true,
          },
          {
            key: "tracker",
            done: signedIn,
            title: `Check the issue tracker${tracker ? `: ${tracker.title}` : ""}`,
            says: tracker && !signedIn
              ? `${tracker.title} is the default for new goals and its sign-in is missing. Set it up, or make another tracker the default.`
              : "Goals read their issues from an issue tracker. GitHub Issues works as it is; Jira needs its site and a sign-in.",
            to: "/config?kind=trackers",
            button: "Open issue trackers",
            admin: true,
          },
          {
            key: "goal",
            done: (goals.total ?? 0) > 0,
            title: "Create a goal",
            says: "Start from issues that exist, picked or by label, or from a spec or an epic that the planner breaks into tasks.",
            to: "/goals/new",
            button: "New goal",
          },
          {
            key: "issue",
            done: (tasks.total ?? 0) > 0,
            title: "Give it an issue",
            says: "Put the goal's label on an open issue, or pick issues on the goal's page. Each becomes a task: an agent works on it and opens a pull request.",
            to: "/goals",
            button: "Open goals",
          },
        ]);
      },
      () => setSteps(null),
    );
  }, []);

  if (!steps || steps.every((s) => s.done)) return null;
  const next = steps.find((s) => !s.done)!;
  return (
    <Paper p="lg" mb="lg" data-testid="getting-started" style={{ borderColor: "var(--mantine-primary-color-filled)" }}>
      <SubTitle>Getting started</SubTitle>
      <Text size="sm" c="dimmed" mb="md">
        {steps.filter((s) => s.done).length} of {steps.length} done. Nothing runs until these are, in this order.
      </Text>
      <Stack gap="md">
        {steps.map((step, i) => (
          <Group key={step.key} align="flex-start" wrap="nowrap" gap="sm" data-testid={`step-${step.key}`} data-done={step.done}>
            <ThemeIcon radius="xl" size={26} color={step.done ? "teal" : step === next ? "indigo" : "gray"} variant={step.done ? "filled" : "light"} aria-label={step.done ? "Done" : `Step ${i + 1}`}>
              {step.done ? <IconCheck size={15} /> : <Text size="xs" fw={700}>{i + 1}</Text>}
            </ThemeIcon>
            <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
              <Text size="sm" fw={600} td={step.done ? "line-through" : undefined} c={step.done ? "dimmed" : undefined}>{step.title}</Text>
              {!step.done && <Text size="sm" c="dimmed">{step.says}</Text>}
              {!step.done && step === next && (
                step.admin && !admin ? (
                  <Text size="sm">Only an admin can do this: ask one to.</Text>
                ) : (
                  <div><Button component={Link} to={step.to} size="xs" mt={4}>{step.button}</Button></div>
                )
              )}
              {!step.done && step !== next && (!step.admin || admin) && <Anchor component={Link} to={step.to} size="xs">{step.button}</Anchor>}
            </Stack>
          </Group>
        ))}
      </Stack>
    </Paper>
  );
}
