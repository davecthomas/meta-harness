import { useState } from "react";
import { Anchor, Code, Collapse, Group, Stack, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { diagnose, type Diagnosis } from "./diagnosis";
import { Remedies } from "./Remedies";

// Failed work, said once per kind of failure: what went wrong, why, what to do, the tasks it covers, and the
// action that fixes them all. Ten tasks that failed the same way are one message with one Retry, not ten
// copies of an error. A goal's page and the Overview both show their failures this way.

// One thing that failed: a task, or an issue the planner refused, which has no task.
export interface FailedItem {
  key: string;
  name: string;
  taskId?: string | null;
  issueRef?: string;
  status: string | null;
  reason: string | null;
  waitingOn?: string | null;
  refused?: string | null;
  max_usd?: string | null;
}

export interface FailureGroup {
  diagnosis: Diagnosis;
  items: FailedItem[];
}

const RETRYABLE = ["failed", "review_unresolved"];

// The items by what went wrong, in the order each kind first appears. An item with nothing wrong is left out.
export function groupFailures(items: FailedItem[]): FailureGroup[] {
  const found = new Map<string, FailureGroup>();
  for (const item of items) {
    const diagnosis = diagnose(item);
    if (!diagnosis) continue;
    const group = found.get(diagnosis.kind);
    if (group) group.items.push(item);
    else found.set(diagnosis.kind, { diagnosis, items: [item] });
  }
  return [...found.values()];
}

function Failure({ group, onChanged }: { group: FailureGroup; onChanged?: () => void }) {
  const [open, setOpen] = useState(false);
  const { diagnosis, items } = group;
  const retryable = items.flatMap((i) => (i.taskId && i.status && RETRYABLE.includes(i.status) && !i.waitingOn ? [{ id: i.taskId, max_usd: i.max_usd }] : []));
  const single = items.length === 1 ? items[0] : null;
  return (
    <Stack gap={4} data-testid={`failure-${diagnosis.kind.split(":")[0]}`}>
      <Text size="sm" fw={600}>
        {diagnosis.summary}{items.length > 1 && ` (${items.length} tasks)`}
      </Text>
      <Text size="sm">{diagnosis.cause}</Text>
      <Text size="sm"><Text span fw={600}>What to do:</Text> {diagnosis.fix}</Text>
      <Group gap="xs" style={{ rowGap: 2 }}>
        {items.map((i) => (i.taskId
          ? <Anchor key={i.key} component={Link} to={`/tasks/${i.taskId}`} size="sm">{i.name}</Anchor>
          : <Text key={i.key} span size="sm">{i.name}</Text>))}
      </Group>
      <Group gap="sm">
        <Remedies
          diagnosis={diagnosis}
          tasks={retryable.length ? retryable : single?.taskId ? [{ id: single.taskId, max_usd: single.max_usd }] : []}
          retry={retryable.length > 0}
          issueRef={single?.issueRef}
          onChanged={onChanged}
        />
        {diagnosis.detail && (
          <Anchor component="button" type="button" size="xs" c="dimmed" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            {open ? "Hide the recorded error" : "Show the recorded error"}
          </Anchor>
        )}
      </Group>
      <Collapse expanded={open}>
        <Code block>{diagnosis.detail}</Code>
      </Collapse>
    </Stack>
  );
}

export function FailureGroups({ groups, onChanged }: { groups: FailureGroup[]; onChanged?: () => void }) {
  return (
    <Stack gap="md">
      {groups.map((group) => <Failure key={group.diagnosis.kind} group={group} onChanged={onChanged} />)}
    </Stack>
  );
}
