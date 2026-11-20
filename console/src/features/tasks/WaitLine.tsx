import { Group, Text } from "@mantine/core";
import { IconHourglass, IconLock } from "@tabler/icons-react";
import type { Task } from "../../api";
import { TaskName } from "../../components/TaskName";
import { holdWords } from "./hold";

// Why a queued or blocked task waits, in one line, wherever a task is listed: a card on a goal's board, a
// row of the task table. Waiting so two tasks never change the same files at once is a reason to wait, and
// says which tasks it waits for. Nothing for a task that does not wait.
export function WaitLine({ task }: { task: Pick<Task, "status" | "held" | "depends_on"> }) {
  if (task.status === "blocked") {
    const n = task.depends_on?.length ?? 0;
    return (
      <Group gap={4} wrap="nowrap" data-testid="wait-line">
        <IconLock size={12} color="var(--mantine-color-yellow-6)" style={{ flex: "none" }} />
        <Text size="xs" c="dimmed">Waits for {n === 1 ? "another issue" : n ? `${n} other issues` : "other issues"} to be done</Text>
      </Group>
    );
  }
  if (task.status !== "queued" || !task.held) return null;
  const holders = task.held.kind === "repo_area" ? ((task.held.detail?.holders as string[] | undefined) ?? []) : [];
  return (
    <Group gap={4} wrap="wrap" data-testid="wait-line" onClick={(e) => e.stopPropagation()}>
      <IconHourglass size={12} color="var(--mantine-color-gray-6)" style={{ flex: "none" }} />
      {holders.length ? (
        <Text size="xs" c="dimmed" component="div">
          Waits for{" "}
          {holders.map((id, n) => (
            <span key={id}>
              {n > 0 && (n === holders.length - 1 ? " and " : ", ")}
              <TaskName id={id} size="xs" />
            </span>
          ))}
          , which {holders.length === 1 ? "is" : "are"} changing the same files
        </Text>
      ) : (
        <Text size="xs" c="dimmed">{holdWords(task.held).line}</Text>
      )}
    </Group>
  );
}
