import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Anchor, Badge, Group, Stack, Text } from "@mantine/core";
import { api, type Trackers } from "../../api";

// The issue trackers that are set up, which is the default for new goals, and whether each is signed in.
export function ConnectedTrackers({ admin = false }: { admin?: boolean }) {
  const [found, setFound] = useState<Trackers | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.trackers().then(setFound, (err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  if (error) return <Text size="sm" c="red">The trackers could not be read: {error}. Reload to try again.</Text>;
  if (!found) return null;
  return (
    <Stack gap="xs">
      {(found.trackers ?? []).filter((t) => t.configured).map((t) => (
        <Group key={t.scheme} justify="space-between">
          <Text size="sm">
            {t.title}
            {t.scheme === found.default ? ", the default for new goals" : ""}
          </Text>
          <Badge color={t.signed_in ? "teal" : "orange"} variant="light" tt="none">
            {t.signed_in ? "Connected" : `Sign-in missing: ${t.missing_credentials.join(", ")}`}
          </Badge>
        </Group>
      ))}
      <Text size="xs" c="dimmed">
        {admin ? <Anchor component={Link} to="/config?kind=trackers" size="xs">Set up trackers and choose the default</Anchor> : "An admin sets up trackers and chooses the default, under Configuration."}
      </Text>
    </Stack>
  );
}
