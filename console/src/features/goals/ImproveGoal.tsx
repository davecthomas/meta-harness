// Created by the meta-harness in run t-46c9a7c1ad78:run-1 on harness:pi-careful/claude:claude-opus-5-5.

import { useState } from "react";
import { Alert, Button, Group, List, Paper, SimpleGrid, Stack, Text, Textarea } from "@mantine/core";
import { IconSparkles } from "@tabler/icons-react";
import { api } from "../../api";
import type { EnrichRequest, GoalProposal } from "./api";

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// "Improve with AI" beside a goal's description. A model proposes a more detailed statement; it shows beside the
// statement it was made from, with what it added and the questions only the operator can answer. The operator
// edits the proposal, then accepts or discards it. Nothing changes until Accept, and a failed call leaves the
// description as it was typed.
export function ImproveGoal({
  statement,
  context,
  onAccept,
}: {
  // The description as it is now: what the proposal is made from.
  statement: string;
  // What else is known about the goal: its id on a goal's page, or what the New goal form has so far.
  context: Omit<EnrichRequest, "statement">;
  // Takes the accepted text: fills the form's field, or saves it on the goal. A refusal throws, for this to show.
  onAccept: (statement: string) => Promise<void> | void;
}) {
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The proposal and the statement it was made from, while the operator decides.
  const [proposal, setProposal] = useState<(GoalProposal & { original: string }) | null>(null);
  const [edited, setEdited] = useState("");
  const [accepting, setAccepting] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);

  async function ask() {
    const original = statement;
    setAsking(true);
    setError(null);
    try {
      const made = await api.enrichGoal({ ...context, statement: original.trim() });
      setProposal({ ...made, original });
      setEdited(made.statement);
      setRefused(null);
    } catch (err) {
      setError(message(err));
    } finally {
      setAsking(false);
    }
  }

  function discard() {
    setProposal(null);
    setRefused(null);
  }

  async function accept() {
    const text = edited.trim();
    if (!text) {
      setRefused("The proposal is empty. Write the description, or discard the proposal.");
      return;
    }
    setAccepting(true);
    try {
      await onAccept(text);
      setProposal(null);
      setRefused(null);
    } catch (err) {
      setRefused(`It was not saved: ${message(err)}. The description is as it was; accept again, or discard.`);
    } finally {
      setAccepting(false);
    }
  }

  return (
    <Stack gap="xs" data-testid="improve-goal">
      {!proposal && (
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
          {!statement.trim() && <Text size="xs" c="dimmed">Write a description first: a model proposes a more detailed one from it.</Text>}
        </Group>
      )}
      {error && (
        <Alert color="orange" title="No proposal was made" data-testid="improve-failed" withCloseButton onClose={() => setError(null)}>
          <Text size="sm">{error}</Text>
          <Text size="sm" mt={4}>The description is kept as you typed it, and the goal can still be saved. Try again later.</Text>
        </Alert>
      )}
      {proposal && (
        <Paper withBorder p="sm" data-testid="improve-proposal">
          <Stack gap="sm">
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
              <Stack gap={4}>
                <Text size="sm" fw={600}>Original</Text>
                <Text size="sm" style={{ whiteSpace: "pre-wrap" }} data-testid="improve-original">{proposal.original}</Text>
              </Stack>
              <Stack gap={4}>
                <Text size="sm" fw={600}>Proposal</Text>
                <Textarea
                  aria-label="Proposed description"
                  autosize
                  minRows={4}
                  maxLength={4000}
                  value={edited}
                  onChange={(e) => setEdited(e.currentTarget.value)}
                />
              </Stack>
            </SimpleGrid>
            {proposal.added.length > 0 && (
              <Stack gap={4} data-testid="improve-added">
                <Text size="sm" fw={600}>What was added</Text>
                <List size="sm" spacing={2}>
                  {proposal.added.map((a, i) => (
                    <List.Item key={i}>
                      {a.what} <Text span size="sm" c="dimmed">{a.why}</Text>
                    </List.Item>
                  ))}
                </List>
              </Stack>
            )}
            {proposal.questions.length > 0 && (
              <Stack gap={4} data-testid="improve-questions">
                <Text size="sm" fw={600}>Open questions</Text>
                <Text size="xs" c="dimmed">Only you can answer these. Write the answers into the proposal before you accept it.</Text>
                <List size="sm" spacing={2}>
                  {proposal.questions.map((q, i) => <List.Item key={i}>{q}</List.Item>)}
                </List>
              </Stack>
            )}
            {refused && <Text size="sm" c="red" data-testid="improve-refused">{refused}</Text>}
            <Group justify="flex-end" gap="sm">
              <Button size="compact-sm" variant="default" onClick={discard} disabled={accepting}>Discard</Button>
              <Button size="compact-sm" onClick={() => void accept()} loading={accepting}>Accept</Button>
            </Group>
          </Stack>
        </Paper>
      )}
    </Stack>
  );
}
