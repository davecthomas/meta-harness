import { useEffect, useRef, useState } from "react";
import { Button, Text, Tooltip } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { useLocation } from "react-router-dom";
import { api } from "../api";
import { anyUnsaved } from "./UnsavedChanges";

// A page open across a deploy keeps running the console it loaded, so new buttons and fixes stay out of it: an
// operator back at a tab left open overnight saw a page that two fixes had already changed. This reads the
// deployed commit every minute, and at once when the tab is shown again. Once it is not the one the page loaded
// with, the page loads the new console by itself at the next safe moment: when the tab is shown again, or on
// the next move to another page. An editor holding unsaved changes holds the reload off, and the button here
// stays for the operator to press when they are ready. A reload the page did by itself says so once it is back,
// in a line in the header that goes after a few seconds, so the operator knows why the page started again.

export const EVERY_MS = 60_000;

// Loads the console again. Kept apart so a test can see it asked.
export const reloader = { reload: () => window.location.reload() };

// Where a reload the page did by itself leaves the version it loaded, for the page that comes back to say so.
export const RELOADED_FOR = "mh:reloaded-for";

// Reloads by itself, leaving word for the page that loads next.
function reloadBySelf(version: string): void {
  try {
    window.sessionStorage.setItem(RELOADED_FOR, version || "new");
  } catch {
    // With no session storage the reload still happens; it is only not announced.
  }
  reloader.reload();
}

// The version a reload the page did by itself was for, read once and forgotten; null when there was none.
export function reloadedFor(): string | null {
  try {
    const version = window.sessionStorage.getItem(RELOADED_FOR);
    window.sessionStorage.removeItem(RELOADED_FOR);
    return version;
  } catch {
    return null;
  }
}

// How long the header says the page updated itself.
export const SAID_MS = 10_000;

export function NewVersion() {
  const loaded = useRef<string | null>(null);
  // The version now deployed, once it is not the one this page loaded: "" when the service names none.
  const [newer, setNewer] = useState<string | null>(null);
  const where = useLocation();
  const first = useRef(where.key);

  // A quiet line in the header, where the button was, for a few seconds: no box over the page, nothing to close.
  const [updated, setUpdated] = useState<string | null>(null);
  useEffect(() => {
    const version = reloadedFor();
    if (!version) return;
    setUpdated(version);
    const timer = setTimeout(() => setUpdated(null), SAID_MS);
    return () => clearTimeout(timer);
  }, []);

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
    const shown = () => {
      if (!document.hidden) void look();
    };
    document.addEventListener("visibilitychange", shown);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", shown);
    };
  }, []);

  // Found while the tab was hidden, or found as the operator comes back to it: nothing on screen is in the
  // middle of being read, so the new console loads now.
  useEffect(() => {
    if (newer === null || anyUnsaved()) return;
    if (document.hidden) {
      const back = () => {
        if (!document.hidden && !anyUnsaved()) reloadBySelf(newer);
      };
      document.addEventListener("visibilitychange", back);
      return () => document.removeEventListener("visibilitychange", back);
    }
  }, [newer]);

  // A move to another page is a fresh start anyway: the new console loads there.
  useEffect(() => {
    if (newer !== null && where.key !== first.current && !anyUnsaved()) reloadBySelf(newer);
  }, [newer, where.key]);

  if (newer === null) {
    if (!updated) return null;
    return (
      <Text size="xs" c="teal" fw={500} role="status" data-testid="updated-note">
        {updated === "new" ? "Updated just now" : `Updated to version ${updated}`}
      </Text>
    );
  }
  return (
    <Tooltip label="A newer version was deployed since this page opened. It loads when you next change page." withArrow>
      <Button size="xs" variant="filled" color="teal" leftSection={<IconRefresh size={14} />} onClick={() => reloader.reload()}>
        {newer ? `Load version ${newer}` : "Load the new version"}
      </Button>
    </Tooltip>
  );
}
