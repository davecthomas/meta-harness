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
  const [newer, setNewer] = useState(false);
  useEffect(() => {
    const look = () =>
      api.version().then(
        (running) => {
          if (!running.commit) return;
          if (loaded.current === null) loaded.current = running.commit;
          else if (running.commit !== loaded.current) setNewer(true);
        },
        () => undefined,
      );
    void look();
    const timer = setInterval(() => void look(), EVERY_MS);
    return () => clearInterval(timer);
  }, []);
  if (!newer) return null;
  return (
    <Tooltip label="A newer version was deployed since this page opened" withArrow>
      <Button size="xs" variant="light" color="teal" leftSection={<IconRefresh size={14} />} onClick={() => window.location.reload()}>
        Reload for the new version
      </Button>
    </Tooltip>
  );
}
