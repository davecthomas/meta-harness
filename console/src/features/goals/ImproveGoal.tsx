// Created by the meta-harness in run t-46c9a7c1ad78:run-1 on harness:pi-careful/claude:claude-opus-5-5.

import { useState } from "react";
import { Alert, Anchor, Button, Group, List, Stack, Text } from "@mantine/core";
import { IconSparkles } from "@tabler/icons-react";
import { api } from "../../api";
import type { EnrichRequest, GoalProposal } from "./api";
import { SubTitle } from "../../components/ui";

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// "Improve with AI" beside a goal's description. A model writes a more detailed statement and it replaces the
// description at once: the operator asked for it, so there is no proposal to compare and accept. What was
// there is one click away (Undo), and the questions only the operator can answer are listed under the field,
// to be answered in the description itself. A failed call leaves the description as it was typed.
export function ImproveGoal({
  statement,
  context,
  onAccept,
}: {
  // The description as it is now: what the new one is written from.
  statement: string;
  // What else is known about the goal: its id on a goal's page, or what the New goal form has so far.
  context: Omit<EnrichRequest, "statement">;
  // Takes the new text: fills the form's field, or saves it on the goal. A refusal throws, for this to show.
  onAccept: (statement: string) => Promise<void> | void;
}) {
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // What the model wrote and the description it replaced, until the operator moves on.
  const [done, setDone] = useState<(GoalProposal & { original: string }) | null>(null);
  const [undoing, setUndoing] = useState(false);

  async function ask() {
    const original = statement;
    setAsking(true);
    setError(null);
    setDone(null);
    let made: GoalProposal;
    try {
      made = await api.enrichGoal({ ...context, statement: original.trim() });
    } catch (err) {
      setError(`${message(err)} The description is kept as you typed it, and the goal can still be saved. Try again later.`);
      setAsking(false);
      return;
    }
    try {
      await onAccept(made.statement);
      setDone({ ...made, original });
    } catch (err) {
      setError(`The new description was not saved: ${message(err)}. The description is as it was. Fix what it names, then try again.`);
    } finally {
      setAsking(false);
    }
  }

  async function undo() {
    if (!done) return;
    setUndoing(true);
    try {
      await onAccept(done.original);
      setDone(null);
    } catch (err) {
      setError(`The earlier description was not put back: ${message(err)}. Try Undo again.`);
    } finally {
      setUndoing(false);
    }
  }

  return (
    <Stack gap="xs" data-testid="improve-goal">
      <Group gap="sm">
        <Button
          size="compact-sm"
          variant="light"
          leftSection={<IconSparkles size={14} />}
          loading={asking}
          disabled={!statement.trim()}
          onClick={() => void ask()}
        >
          Improve with AI
        </Button>
        {!statement.trim() && <Text size="xs" c="dimmed">Write a description first: a model rewrites it with more detail.</Text>}
        {done && (
          <Text size="sm" c="dimmed" data-testid="improve-done">
            Rewritten with more detail.{" "}
            <Anchor component="button" type="button" size="sm" disabled={undoing} onClick={() => void undo()}>Undo</Anchor>
          </Text>
        )}
      </Group>
      {error && (
        <Alert color="orange" title="The description was not improved" data-testid="improve-failed" withCloseButton onClose={() => setError(null)}>
          <Text size="sm">{error}</Text>
        </Alert>
      )}
      {done && done.questions.length > 0 && (
        <Stack gap={4} data-testid="improve-questions">
          <SubTitle>Open questions</SubTitle>
          <Text size="xs" c="dimmed">Only you can answer these. Write the answers into the description.</Text>
          <List size="sm" spacing={2}>
            {done.questions.map((q, i) => <List.Item key={i}>{q}</List.Item>)}
          </List>
        </Stack>
      )}
    </Stack>
  );
}
