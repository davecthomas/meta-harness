import { Alert, Anchor, List, Stack, Text } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import type { MhEvent, Task } from "../../api";
import { diagnose } from "./diagnosis";
import { Remedies } from "./Remedies";

const TITLES: Record<string, string> = {
  failed: "This task failed",
  review_unresolved: "The review loop ended without a clean review",
};

// A run that ended without finishing, from its attempt_finished event.
function unfinished(events: MhEvent[]): MhEvent[] {
  return events.filter((e) => e.event_type === "attempt_finished" && e.payload.status !== "FINISHED");
}

// A failed or unresolved task says so at the top of its page: why it ended, each run that did not finish, and
// where its log lines are. Nothing for a task in any other status.
export function TaskFailure({ task, status, events, onChanged }: { task: Task; status: string; events: MhEvent[]; onChanged?: () => void }) {
  const title = TITLES[status];
  if (!title) return null;
  const runs = unfinished(events);
  const diagnosis = diagnose({ status, reason: task.status_reason });
  return (
    <Alert color={status === "failed" ? "red" : "orange"} title={title} icon={<IconAlertTriangle size={18} />} mb="md" role="alert">
      <Stack gap="xs">
        {diagnosis && (
          <>
            <Text size="sm" fw={600}>{diagnosis.summary}</Text>
            <Text size="sm">{diagnosis.cause}</Text>
            <Text size="sm"><Text span fw={600}>What to do:</Text> {diagnosis.fix}</Text>
            <Remedies diagnosis={{ ...diagnosis, remedies: diagnosis.remedies.filter((r) => r !== "task") }} tasks={[task]} issueRef={task.external_ref} onChanged={onChanged} />
            {diagnosis.detail && <Text size="xs" c="dimmed" ff="monospace" style={{ wordBreak: "break-word" }}>{diagnosis.detail}</Text>}
          </>
        )}
        {runs.length > 0 && (
          <List size="sm" spacing={2}>
            {runs.map((e) => (
              <List.Item key={e.position}>
                <Anchor component={Link} to={`/tasks/${task.id}?run=${encodeURIComponent(e.run_id ?? "")}`} size="sm">
                  Run {e.payload.attempt}
                </Anchor>{" "}
                ({e.payload.tier}) on {e.payload.candidate}: {e.payload.detail}
              </List.Item>
            ))}
          </List>
        )}
        <Anchor component={Link} to={`/admin/logs?q=${encodeURIComponent(task.id)}&minutes=1440`} size="sm">
          Warnings and errors logged for this task
        </Anchor>
      </Stack>
    </Alert>
  );
}
