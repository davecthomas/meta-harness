import { useEffect, useState } from "react";
import { Anchor, Center, Group, Loader, Modal, Progress, Stack, Table, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { api, type GoalSpend as Spent } from "../../api";
import { DataTable, ErrorAlert, StatusBadge } from "../../components/ui";

const KINDS: [string, string][] = [["task", "Tasks' own runs"], ["review", "Reviews"], ["fix", "Fixes"]];

// Where a goal's money went, opened from its spend line: how much went to the tasks' own runs, to reviews and
// to fixes, and each task with what its runs spent, the dearest first.
export function GoalSpend({ goalId, opened, onClose }: { goalId: string; opened: boolean; onClose: () => void }) {
  const [spend, setSpend] = useState<Spent | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!opened) return;
    setSpend(null);
    api.goalSpend(goalId).then(setSpend, (err) => setError(err instanceof Error ? err.message : String(err)));
  }, [goalId, opened]);
  const total = spend ? Number(spend.usd) : 0;
  return (
    <Modal opened={opened} onClose={onClose} title="Where the goal's money went" size="xl">
      <ErrorAlert error={error} />
      {!spend && !error && <Center h={120}><Loader /></Center>}
      {spend && (
        <Stack gap="md">
          <Text size="sm">
            <Text span fw={650}>${total.toFixed(2)}</Text> spent{spend.max_usd ? ` of a $${Number(spend.max_usd).toFixed(2)} budget` : ""}, across {spend.tasks.length} tasks.
          </Text>
          <Group gap="xl" data-testid="goal-spend-kinds">
            {KINDS.map(([kind, name]) => {
              const usd = Number(spend.by_kind[kind] ?? 0);
              return (
                <Stack key={kind} gap={2} w={180}>
                  <Text size="xs" c="dimmed">{name}: ${usd.toFixed(2)}{total ? ` (${Math.round((100 * usd) / total)}%)` : ""}</Text>
                  <Progress value={total ? (100 * usd) / total : 0} color={kind === "review" ? "grape" : kind === "fix" ? "orange" : "blue"} />
                </Stack>
              );
            })}
          </Group>
          <DataTable head={["Task", "Status", "Runs", "Turns", "Own runs", "Reviews", "Fixes", "Total"]} minWidth={760}>
            {spend.tasks.map((task) => (
              <Table.Tr key={task.task_id} data-testid={`goal-spend-${task.task_id}`}>
                <Table.Td><Anchor component={Link} to={`/tasks/${task.task_id}?tab=spend`} size="sm">{task.title ?? task.task_id}</Anchor></Table.Td>
                <Table.Td><StatusBadge status={task.status} /></Table.Td>
                <Table.Td><Text size="sm">{task.runs}</Text></Table.Td>
                <Table.Td><Text size="sm">{task.turns}</Text></Table.Td>
                {(["task", "review", "fix"] as const).map((kind) => (
                  <Table.Td key={kind}><Text size="sm">${Number(task.by_kind[kind] ?? 0).toFixed(2)}</Text></Table.Td>
                ))}
                <Table.Td><Text size="sm" fw={650}>${Number(task.usd).toFixed(2)}</Text></Table.Td>
              </Table.Tr>
            ))}
          </DataTable>
        </Stack>
      )}
    </Modal>
  );
}
