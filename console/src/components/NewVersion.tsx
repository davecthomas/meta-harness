import { useEffect, useRef, useState } from "react";
import { Button, Tooltip } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { api } from "../api";

// A page open across a deploy keeps running the console it loaded, so new buttons and fixes stay out of it.
// This reads the deployed commit every minute and, once it is not the one the page loaded with, offers to load
// the new one.

const EVERY_MS = 60_000;

export function NewVersion() {
  const loaded = useRef<string | null>(null);
  // The version now deployed, once it is not the one this page loaded: "" when the service names none.
  const [newer, setNewer] = useState<string | null>(null);
  useEffect(() => {
    const look = () =>
      api.version().then(
        (running) => {
          if (!running.commit) return;
          if (loaded.current === null) loaded.current = running.commit;
          else if (running.commit !== loaded.current) setNewer(running.version ?? "");
        },
        () => undefined,
      );
    void look();
    const timer = setInterval(() => void look(), EVERY_MS);
    return () => clearInterval(timer);
  }, []);
  if (newer === null) return null;
  return (
    <Tooltip label="A newer version was deployed since this page opened" withArrow>
      <Button size="xs" variant="light" color="teal" leftSection={<IconRefresh size={14} />} onClick={() => window.location.reload()}>
        {newer ? `Reload for version ${newer}` : "Reload for the new version"}
      </Button>
    </Tooltip>
  );
}
