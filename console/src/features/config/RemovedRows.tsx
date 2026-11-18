import { Button, Group, Paper, Stack, Text } from "@mantine/core";
import { IconArrowBackUp } from "@tabler/icons-react";
import type { Staging } from "./ConfigViews";

// One row an operator removed that is still in the active version: its name, and how to put it back.
export interface Removed {
  name: string;
  restore: () => void;
}

// The entries of a document's map (repos, profiles, skills) that the staged changes removed. Putting one
// back stages it as the active version has it, so the removal is no longer a change.
export function removedEntries(s: Staging, key: string): Removed[] {
  const before: Record<string, unknown> = s.original[key] ?? {};
  const now: Record<string, unknown> = s.document[key] ?? {};
  return Object.keys(before)
    .filter((name) => !(name in now))
    .map((name) => ({ name, restore: () => s.stage([key, name], before[name]) }));
}

// Under a table or list: each row removed and not saved yet, with Undo. A removed row leaves the view at
// once, so without this the only ways back were to discard every staged change or to add the row again by hand.
export function RemovedRows({ what, rows, compact }: { what: string; rows: Removed[]; compact?: boolean }) {
  if (!rows.length) return null;
  return (
    <Paper withBorder={!compact} p={compact ? 0 : "sm"} data-testid={`removed-${what}`} style={compact ? undefined : { borderColor: "var(--mantine-color-yellow-5)" }}>
      <Stack gap={4}>
        {rows.map((row, i) => (
          <Group key={`${row.name}-${i}`} gap="xs" wrap="nowrap" justify="space-between">
            <Text size={compact ? "xs" : "sm"}>
              Removed, not saved yet: {what} <Text span fw={600} ff="monospace" size={compact ? "xs" : "sm"}>{row.name}</Text>
            </Text>
            <Button size="compact-xs" variant="default" leftSection={<IconArrowBackUp size={14} />} aria-label={`Undo removing ${what} ${row.name}`} onClick={row.restore}>
              Undo
            </Button>
          </Group>
        ))}
      </Stack>
    </Paper>
  );
}
