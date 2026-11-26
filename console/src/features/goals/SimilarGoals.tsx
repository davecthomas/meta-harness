import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Anchor, Button, Group, Paper, Stack, Text } from "@mantine/core";
import { IconExternalLink } from "@tabler/icons-react";
import { call } from "../../client";
import { StatusBadge, SubTitle, when } from "../../components/ui";

// The open goals a new goal's description is close to (docs/specs/similar-goals.md), so an operator does not
// start the same work twice. The planner compares; this shows the closest few under the description, each with
// what the operator can do about it, and asks one question at save while a row still shows. Nothing here
// blocks a save. Every answer is recorded, which is what the cut-off is set from.

export interface SimilarGoal {
  goal_id: string;
  title: string;
  status: string;
  since: string;
  // Cosine similarity, 0 to 1.
  score: number;
}

type Answer = "opened" | "not_duplicate" | "same_work" | "different";

// How long after typing stops the description is compared.
export const WAIT_MS = 2000;
// A description shorter than this says too little to compare; the planner holds the same line.
export const MIN_CHARS = 40;

export const similarApi = {
  similarGoals: (description: string) => call<{ goals: SimilarGoal[]; checked: boolean }>("/v1/goals/similar", { method: "POST", body: JSON.stringify({ description }) }),
  answerSimilar: (goal: SimilarGoal, answer: Answer, newGoalId = "") =>
    call<{ recorded: string }>("/v1/goals/similar/answer", { method: "POST", body: JSON.stringify({ goal_id: goal.goal_id, score: goal.score, answer, new_goal_id: newGoalId }) }),
};

const percent = (score: number) => `${Math.round(score * 100)}% similar`;
const say = (err: unknown) => (err instanceof Error ? err.message : String(err));

export interface Similar {
  // The panel, for under the description. Null when there is nothing to show.
  panel: ReactNode;
  // Called before a save: resolves true when the save may go on. With a row still showing it asks the one
  // question first, in the panel, and resolves when the operator has answered.
  beforeSave: () => Promise<boolean>;
  // The goal was saved: what the operator said is recorded with the goal it became.
  saved: (goalId: string) => void;
}

export function useSimilarGoals(description: string): Similar {
  const [rows, setRows] = useState<SimilarGoal[]>([]);
  const [failed, setFailed] = useState<string | null>(null);
  // The question at save: the row asked about, and whether the operator has said it is the same work.
  const [asking, setAsking] = useState<{ goal: SimilarGoal; same: boolean } | null>(null);
  const answer = useRef<((go: boolean) => void) | null>(null);
  const said = useRef<{ goal: SimilarGoal; answer: Answer } | null>(null);
  const checked = useRef("");

  const text = description.trim();
  useEffect(() => {
    if (text.length < MIN_CHARS) {
      setRows([]);
      setFailed(null);
      checked.current = "";
      return;
    }
    if (text === checked.current) return;
    const timer = window.setTimeout(() => {
      checked.current = text;
      similarApi.similarGoals(text).then(
        (found) => {
          setRows(Array.isArray(found?.goals) ? found.goals : []);
          setFailed(null);
        },
        (err) => {
          setRows([]);
          setFailed(say(err));
        },
      );
    }, WAIT_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  const record = (goal: SimilarGoal, what: Answer, newGoalId = "") => void similarApi.answerSimilar(goal, what, newGoalId).catch(() => undefined);

  const beforeSave = useCallback(() => {
    if (!rows.length || said.current) return Promise.resolve(true);
    setAsking({ goal: rows[0], same: false });
    return new Promise<boolean>((resolve) => {
      answer.current = resolve;
    });
  }, [rows]);

  const decide = (go: boolean) => {
    answer.current?.(go);
    answer.current = null;
    if (go) setAsking(null);
  };

  const saved = useCallback((goalId: string) => {
    // "Different" is recorded once the goal exists, so the answer names it.
    if (said.current?.answer === "different") record(said.current.goal, "different", goalId);
  }, []);

  let panel: ReactNode = null;
  if (asking) {
    const { goal, same } = asking;
    panel = (
      <Paper withBorder p="md" radius="md" data-testid="similar-question" role="group" aria-label="Is this the same work?">
        <Stack gap="sm">
          <Text size="sm" fw={600}>Is this the same work as "{goal.title}"?</Text>
          {!same ? (
            <Group gap="xs">
              <Button
                size="xs"
                variant="default"
                onClick={() => {
                  said.current = { goal, answer: "same_work" };
                  record(goal, "same_work");
                  setAsking({ goal, same: true });
                }}
              >
                Yes, same work
              </Button>
              <Button
                size="xs"
                variant="default"
                onClick={() => {
                  said.current = { goal, answer: "different" };
                  decide(true);
                }}
              >
                No, different
              </Button>
            </Group>
          ) : (
            <Group gap="xs">
              <Button size="xs" component="a" href={`/goals/${goal.goal_id}`} target="_blank" rel="noreferrer" rightSection={<IconExternalLink size={14} />}>
                Open that goal
              </Button>
              <Button size="xs" variant="default" onClick={() => decide(true)}>Create this one anyway</Button>
            </Group>
          )}
        </Stack>
      </Paper>
    );
  } else if (rows.length) {
    panel = (
      <Paper withBorder p="md" radius="md" data-testid="similar-goals">
        <SubTitle>Similar goals already open</SubTitle>
        <Stack gap="sm" mt="xs">
          {rows.map((goal) => (
            <Group key={goal.goal_id} justify="space-between" wrap="wrap" gap="sm" data-testid="similar-goal">
              <Stack gap={2} style={{ flex: "1 1 260px", minWidth: 0 }}>
                <Text size="sm" fw={500} style={{ overflowWrap: "anywhere" }}>{goal.title}</Text>
                <Group gap="xs">
                  <StatusBadge status={goal.status} />
                  <Text size="xs" c="dimmed">since {when(goal.since)} · {percent(goal.score)}</Text>
                </Group>
              </Stack>
              <Group gap="xs">
                <Anchor href={`/goals/${goal.goal_id}`} target="_blank" rel="noreferrer" size="sm" onClick={() => record(goal, "opened")}>
                  Open it
                </Anchor>
                <Button
                  size="compact-xs"
                  variant="default"
                  onClick={() => {
                    record(goal, "not_duplicate");
                    setRows((now) => now.filter((row) => row.goal_id !== goal.goal_id));
                  }}
                >
                  Not a duplicate
                </Button>
              </Group>
            </Group>
          ))}
        </Stack>
      </Paper>
    );
  } else if (failed) {
    panel = <Text size="xs" c="dimmed" data-testid="similar-failed">Could not look for similar goals: {failed}. You can still save.</Text>;
  }
  return { panel, beforeSave, saved };
}
