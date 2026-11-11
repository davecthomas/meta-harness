import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Group, Paper, Stack, Text, Tooltip } from "@mantine/core";
import { api } from "../../api";
import { SectionTitle, when } from "../../components/ui";
import type { PullRequestCell, PullRequests } from "./api";

// What each pull request state means, and its color: the two that need an operator are the strongest.
export const PR_STATES = [
  { key: "blocked", label: "Blocked", color: "var(--mantine-color-red-6)" },
  { key: "waiting", label: "Waiting", color: "var(--mantine-color-orange-5)" },
  { key: "in_review", label: "In review", color: "var(--mantine-color-blue-5)" },
  { key: "merged", label: "Merged", color: "var(--mantine-color-teal-3)" },
  { key: "closed", label: "Closed", color: "var(--mantine-color-gray-5)" },
] as const;
const STATE_COLOR = Object.fromEntries(PR_STATES.map((s) => [s.key, s.color])) as Record<string, string>;
const CELL = 14;

function Cell({ pr, open }: { pr: PullRequestCell; open: (pr: PullRequestCell) => void }) {
  const state = pr.state.replace("_", " ");
  const label = (
    <Stack gap={2}>
      <Text size="xs" fw={600}>{pr.title}</Text>
      <Text size="xs">{pr.repo} #{pr.pr_url.split("/").pop()} · {state}</Text>
      <Text size="xs">Opened {when(pr.opened_at)}, {state} since {when(pr.state_at)}</Text>
      <Text size="xs">{pr.fix_rounds} fix rounds</Text>
    </Stack>
  );
  return (
    <Tooltip label={label} withArrow>
      <Box
        role="link"
        tabIndex={0}
        aria-label={`${pr.title}: ${pr.state}`}
        data-testid={`cell-${pr.task_id}`}
        data-state={pr.state}
        onClick={() => open(pr)}
        onKeyDown={(e) => e.key === "Enter" && open(pr)}
        style={{ width: CELL, height: CELL, borderRadius: 3, background: STATE_COLOR[pr.state], cursor: "pointer" }}
      />
    </Tooltip>
  );
}

// Every pull request of the last 13 weeks in one strip, oldest on the left, colored by state, with how many are
// blocked or waiting on an operator now. A cell opens its task's latest run.
export function PullRequestsRow() {
  const navigate = useNavigate();
  const [found, setFound] = useState<PullRequests | null>(null);
  useEffect(() => {
    api.pullRequests().then(setFound, () => setFound(null));
  }, []);
  if (!found || !found.prs.length) return null;
  const open = (pr: PullRequestCell) =>
    navigate(`/tasks/${pr.task_id}` + (pr.latest_run_id ? `?run=${encodeURIComponent(pr.latest_run_id)}` : ""));
  const count = (state: string) => found.prs.filter((p) => p.state === state).length;
  return (
    <>
      <SectionTitle>Pull requests</SectionTitle>
      <Text size="sm" c="dimmed" mb="xs">
        {count("blocked")} blocked · {count("waiting")} waiting for you
        {found.left_out > 0 && ` · ${found.left_out} older left out`}
      </Text>
      <Paper p="sm" mb="lg" withBorder>
        <Group gap={3} data-testid="pull-requests-row">
          {found.prs.map((pr) => <Cell key={pr.task_id} pr={pr} open={open} />)}
        </Group>
        <Group gap="md" mt="xs">
          {PR_STATES.map((s) => (
            <Group key={s.key} gap={4}>
              <Box w={10} h={10} style={{ borderRadius: 2, background: s.color }} />
              <Text size="xs">{s.label} {count(s.key)}</Text>
            </Group>
          ))}
        </Group>
      </Paper>
    </>
  );
}
