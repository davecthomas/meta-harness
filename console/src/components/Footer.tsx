// Created by the meta-harness in run t-fc9eba5bd270:run-1 on harness:pi-careful/claude:claude-opus-5-5.

import { useEffect, useState } from "react";
import { Anchor, Flex, Group, Image, Text, Tooltip } from "@mantine/core";
import { api } from "../api";
import { TAGLINE } from "../brand/Splash";
import symbol from "../brand/octofusion-symbol.svg";

export const REPOSITORY = "https://github.com/davecthomas/meta-harness";

// The foot of every page, after its content: it scrolls with the page. One dimmed line on a wide screen; on a
// phone its parts stack. version: whether to read and show what runs. The sign-in screen leaves it out, since
// /v1/version needs a session and the edge refuses it without one.
export function Footer({ version = true }: { version?: boolean }) {
  return (
    <Flex
      component="footer"
      data-testid="footer"
      direction={{ base: "column", sm: "row" }}
      align="center"
      justify="center"
      gap={{ base: 4, sm: "md" }}
      mt="xl"
      pt="md"
      pb="xs"
      style={{ borderTop: "1px solid var(--mantine-color-default-border)" }}
    >
      <Group gap={6} wrap="nowrap">
        <Image src={symbol} alt="" h={20} w={20} fit="contain" />
        <Text size="xs" c="dimmed" fw={600}>Octofusion AI</Text>
      </Group>
      <Text size="xs" c="dimmed" ta="center">{TAGLINE}</Text>
      {version && <VersionTag />}
      <Anchor href={REPOSITORY} target="_blank" rel="noreferrer" size="xs" c="dimmed">GitHub</Anchor>
    </Flex>
  );
}

// The version and the deployed commit. The commit links to it on GitHub.
function VersionTag() {
  const [running, setRunning] = useState<{ version: string; commit: string | null } | null>(null);
  useEffect(() => {
    api.version().then(setRunning, () => setRunning(null));
  }, []);
  if (!running) return null;
  return (
    <Text size="xs" c="dimmed" data-testid="version">
      v{running.version}
      {running.commit && (
        <>
          {" · "}
          <Tooltip label={running.commit}>
            <Anchor href={`${REPOSITORY}/commit/${running.commit}`} target="_blank" rel="noreferrer" c="dimmed" size="xs" ff="monospace">
              {running.commit.slice(0, 7)}
            </Anchor>
          </Tooltip>
        </>
      )}
    </Text>
  );
}
