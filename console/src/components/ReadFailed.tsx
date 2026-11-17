import { Text } from "@mantine/core";

// What a section shows when it could not read what it lists: that it failed, why, and what to do. A section
// never shows its empty state, or nothing at all, for a read that failed: "none" must mean there are none.
export function ReadFailed({ what, error, retries }: { what: string; error: string; retries?: string }) {
  return (
    <Text size="sm" c="red" mb="sm" role="alert" data-testid="read-failed">
      Could not read {what}: {error}. {retries ?? "Reload the page to try again."}
    </Text>
  );
}

export const errorText = (err: unknown): string => (err instanceof Error ? err.message : String(err));
