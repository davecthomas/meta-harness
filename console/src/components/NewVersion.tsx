import { useEffect, useRef, useState } from "react";
import { Text } from "@mantine/core";
import { useLocation } from "react-router-dom";
import { api } from "../api";
import { anyUnsaved } from "./UnsavedChanges";

// A page open across a deploy keeps running the console it loaded, so new buttons and fixes stay out of it: an
// operator back at a tab left open overnight saw a page that two fixes had already changed. This reads the
// deployed commit every minute, and at once when the tab is shown again. Once it is not the one the page loaded
// with, the page loads the new console by itself. There is nothing to press: a button for it was one more thing
// asked of the operator. It waits only while loading would cost them something (an unsaved edit, a field being
// typed in, an open dialog), and says it updated in a line in the header that goes after a few seconds.

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

// How often a held reload is tried again.
export const RETRY_MS = 5000;

// Whether loading the console again now would cost the operator something: an unsaved edit, a field they are
// typing in, or a dialog they have open.
export function busy(): boolean {
  if (anyUnsaved()) return true;
  const at = document.activeElement as HTMLElement | null;
  const typing = !!at && (at.tagName === "TEXTAREA" || at.isContentEditable || (at.tagName === "INPUT" && !["checkbox", "radio", "button", "submit"].includes((at as HTMLInputElement).type)));
  return typing || document.querySelector('[role="dialog"][aria-modal="true"]') !== null;
}

export function NewVersion() {
  const loaded = useRef<string | null>(null);
  // The version now deployed, once it is not the one this page loaded: "" when the service names none.
  const [newer, setNewer] = useState<string | null>(null);
  const where = useLocation();

  // A quiet line in the header for a few seconds: no box over the page, nothing to close.
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

  // The new console loads by itself, with nothing to press, as soon as loading it would cost the operator
  // nothing: no unsaved edit, no field being typed in, no dialog open. Until then it looks again every few
  // seconds, and when the tab is shown again or the operator moves to another page.
  useEffect(() => {
    if (newer === null) return;
    const go = () => {
      if (!document.hidden && !busy()) reloadBySelf(newer);
    };
    go();
    const timer = setInterval(go, RETRY_MS);
    document.addEventListener("visibilitychange", go);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", go);
    };
  }, [newer, where.key]);

  if (!updated) return null;
  return (
    <Text size="xs" c="teal" fw={500} role="status" data-testid="updated-note">
      {updated === "new" ? "Updated just now" : `Updated to version ${updated}`}
    </Text>
  );
}
