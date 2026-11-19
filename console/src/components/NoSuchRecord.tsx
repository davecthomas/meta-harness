import { Link } from "react-router-dom";
import { Alert, Anchor, Group, Text } from "@mantine/core";
import { ApiError } from "../client";

// A task or goal read that the service answered with "no such": nothing failed, the id is not one. It reads
// like the page an unknown address gets, with the ways on.
export const isNotFound = (err: unknown): boolean => err instanceof ApiError && err.status === 404;

export function NoSuchRecord({ kind, id }: { kind: "task" | "goal"; id: string }) {
  return (
    <Alert color="yellow" title="No such page" maw={640} data-testid="no-such-page">
      <Text size="sm" mb="xs">
        No {kind} has the id <Text span ff="monospace">{id}</Text>. The link may be old, or the address mistyped.
      </Text>
      <Group gap="md">
        <Anchor component={Link} to="/tasks" size="sm">Tasks</Anchor>
        <Anchor component={Link} to="/goals" size="sm">Goals</Anchor>
      </Group>
    </Alert>
  );
}
