import { useEffect, useState } from "react";
import { Button, Group, Paper, ScrollArea, Select, SimpleGrid, Stack, Text } from "@mantine/core";
import { IconCheck, IconCopy } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import { api, type AttemptsCompare } from "../../api";
import { ErrorAlert } from "../../components/ui";
import { CandidateName, DecisionBadge } from "./words";
import { diagnose } from "./diagnosis";

// On a task that has not started: run it as several attempts at once, for an operator to compare and pick one.
export function RunAttempts({ taskId, attempts, onSet }: { taskId: string; attempts: number; onSet: () => void }) {
  const [count, setCount] = useState(String(Math.max(2, attempts)));
  const [error, setError] = useState<string | null>(null);
  return (
    <Paper withBorder p="sm" mb="md">
      <Group gap="sm" align="flex-end">
        <Select label="Attempts at once" data={["2", "3", "4"]} value={count} onChange={(v) => setCount(v ?? "2")} w={160} />
        <Button
          leftSection={<IconCopy size={16} />}
          variant="light"
          onClick={async () => {
            try {
              await api.setAttempts(taskId, Number(count));
              notifications.show({ color: "teal", title: `${count} attempts`, message: "The task runs them at once, on its tier's candidates, and waits for your pick." });
              onSet();
            } catch (err) {
              setError(err instanceof Error ? err.message : String(err));
            }
          }}
        >
          Run attempts
        </Button>
        {attempts > 1 && <Text size="sm" c="dimmed">Set to {attempts} attempts.</Text>}
      </Group>
      <ErrorAlert error={error} />
    </Paper>
  );
}

// Why an attempt did not finish: what happened in words where the recorded reason is one the console knows,
// with the reason as recorded under it.
function WhyNot({ detail }: { detail: string }) {
  const known = diagnose({ status: "failed", reason: detail });
  const named = known && !known.kind.startsWith("other:");
  return (
    <Stack gap={0} data-testid="attempt-why">
      {named && <Text size="xs" c="red">{known.summary}</Text>}
      <Text size="xs" c={named ? "dimmed" : "red"}>Recorded: {detail}</Text>
    </Stack>
  );
}

function PatchView({ patch }: { patch: string }) {
  return (
    <ScrollArea.Autosize mah="45vh">
      <div style={{ fontFamily: "var(--mantine-font-family-monospace)", fontSize: 11, whiteSpace: "pre" }}>
        {patch.split("\n").map((line, i) => (
          <div
            key={i}
            style={{
              background: line.startsWith("+") && !line.startsWith("+++") ? "var(--mantine-color-teal-light)" : line.startsWith("-") && !line.startsWith("---") ? "var(--mantine-color-red-light)" : line.startsWith("@@") ? "var(--mantine-color-blue-light)" : undefined,
            }}
          >
            {line}
          </div>
        ))}
      </div>
    </ScrollArea.Autosize>
  );
}

// A task's attempts side by side: each one's candidate, how it ended, cost, files and lines changed, and its diff.
// While the task waits, Pick this one sends the operator's choice; its changes become the pull request.
export function CompareAttempts({ taskId }: { taskId: string }) {
  const [compare, setCompare] = useState<AttemptsCompare | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<number | null>(null);

  useEffect(() => {
    api.attempts(taskId).then(setCompare, (err) => setError(err instanceof Error ? err.message : String(err)));
  }, [taskId]);

  if (error) return <ErrorAlert error={error} />;
  if (!compare) return <Text size="sm" c="dimmed">Reading the attempts…</Text>;
  if (!compare.attempts.length) return <Text size="sm" c="dimmed">No attempt has finished yet.</Text>;
  return (
    <Stack gap="sm">
      {compare.waiting && <Text size="sm">The task waits for you: pick the attempt whose changes become the pull request.</Text>}
      <SimpleGrid cols={{ base: 1, md: Math.min(compare.attempts.length, 4) }}>
        {compare.attempts.map((a) => {
          const added = a.files.reduce((s, f) => s + f.additions, 0);
          const removed = a.files.reduce((s, f) => s + f.deletions, 0);
          return (
            <Paper key={a.attempt} withBorder p="sm" data-testid={`attempt-${a.attempt}`}>
              <Stack gap={6}>
                <Group justify="space-between">
                  <Text fw={650}>Attempt {a.attempt}</Text>
                  <DecisionBadge decision={a.decision} />
                </Group>
                <CandidateName pair={a.candidate} />
                <Text size="xs" c="dimmed">
                  ${Number(a.spent_usd ?? 0).toFixed(2)} spent · {a.files.length} {a.files.length === 1 ? "file" : "files"} changed · {added} {added === 1 ? "line" : "lines"} added, {removed} removed
                </Text>
                {a.decision !== "finished" && a.detail && <WhyNot detail={a.detail} />}
                {a.patch && <PatchView patch={a.patch} />}
                {compare.waiting && a.decision === "finished" && (
                  <Button
                    size="xs"
                    leftSection={<IconCheck size={14} />}
                    disabled={picked !== null}
                    onClick={async () => {
                      try {
                        await api.messageTask(taskId, String(a.attempt), "choose");
                        setPicked(a.attempt);
                        notifications.show({ color: "teal", title: `Attempt ${a.attempt} picked`, message: "Its changes become the pull request." });
                      } catch (err) {
                        setError(err instanceof Error ? err.message : String(err));
                      }
                    }}
                  >
                    {picked === a.attempt ? "Picked" : "Pick this one"}
                  </Button>
                )}
              </Stack>
            </Paper>
          );
        })}
      </SimpleGrid>
    </Stack>
  );
}
