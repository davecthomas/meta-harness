import { useEffect, useState, type ReactNode } from "react";
import { NavLink as RouterLink, useLocation } from "react-router-dom";
import { ActionIcon, Box, Collapse, Group, NavLink, Text } from "@mantine/core";
import { IconChevronRight } from "@tabler/icons-react";
import { statusColor } from "../theme";

// The pieces of the left pane's history: rows that link to a page and open to show what is under them, as other
// agent consoles list their conversations.

// How often an open history list reads its rows again, besides on every page change.
export const REFRESH_MS = 30_000;

// A small dot in a status's color, with the status as its name for a screen reader and on hover.
export function StatusDot({ status }: { status: string }) {
  return (
    <Box
      component="span"
      role="img"
      aria-label={status.replace(/_/g, " ")}
      title={status.replace(/_/g, " ")}
      w={8}
      h={8}
      display="inline-block"
      style={{ borderRadius: "50%", flex: "none", background: `var(--mantine-color-${statusColor(status)}-6)` }}
    />
  );
}

const ROUNDED = { borderRadius: "var(--mantine-radius-md)" };

// A row that links to a page, with a chevron beside it that opens or closes what is under it. The link and the
// chevron are separate controls: following the link never closes the list, and opening the list never navigates.
export function TreeItem(props: {
  label: string;
  to: string;
  active: boolean;
  leftSection?: ReactNode;
  opened: boolean;
  onToggle: () => void;
  children: ReactNode;
  bold?: boolean;
}) {
  return (
    <>
      <Group gap={0} wrap="nowrap">
        <NavLink
          component={RouterLink}
          to={props.to}
          label={<Text size="sm" truncate fw={props.bold ? 600 : undefined}>{props.label}</Text>}
          title={props.label}
          leftSection={props.leftSection}
          active={props.active}
          variant="light"
          style={{ ...ROUNDED, flex: 1, minWidth: 0 }}
        />
        <ActionIcon
          variant="subtle"
          color="gray"
          size="sm"
          onClick={props.onToggle}
          aria-label={`${props.opened ? "Close" : "Open"} ${props.label}`}
          aria-expanded={props.opened}
        >
          <IconChevronRight size={14} style={{ transform: props.opened ? "rotate(90deg)" : undefined, transition: "transform 150ms" }} />
        </ActionIcon>
      </Group>
      <Collapse expanded={props.opened}>
        <Box pl="sm" ml={6} style={{ borderLeft: "1px solid var(--mantine-color-default-border)" }}>
          {props.opened && props.children}
        </Box>
      </Collapse>
    </>
  );
}

// A leaf: one goal's or task's row, linking to its page.
export function LeafItem(props: { label: string; to: string; status: string }) {
  const path = useLocation().pathname;
  return (
    <NavLink
      component={RouterLink}
      to={props.to}
      label={<Text size="sm" truncate>{props.label}</Text>}
      title={props.label}
      leftSection={<StatusDot status={props.status} />}
      active={path === props.to}
      variant="light"
      py={4}
      style={ROUNDED}
    />
  );
}

// A heading row with no page of its own that opens to a list: "Past goals (12)".
export function PastGroup(props: { label: string; defaultOpened?: boolean; children: ReactNode }) {
  const [opened, setOpened] = useState(props.defaultOpened ?? false);
  return (
    <NavLink
      component="button"
      label={<Text size="xs" c="dimmed">{props.label}</Text>}
      opened={opened}
      onChange={setOpened}
      py={4}
      style={ROUNDED}
      childrenOffset={0}
    >
      {/* Always a child, so the row has something to open; read only once it is open. */}
      <>{opened && props.children}</>
    </NavLink>
  );
}

// A short line in the tree: still loading, nothing there, or why it could not be read and what to do.
export function TreeNote({ children, error }: { children: ReactNode; error?: boolean }) {
  return (
    <Text size="xs" c={error ? "red" : "dimmed"} px="sm" py={4}>
      {children}
    </Text>
  );
}

// Rows read by `load`, again on every page change and every REFRESH_MS while shown, so a new task or a status
// change shows without a reload. A failed read keeps the rows it had and says why.
export function useRows<T>(load: () => Promise<T[]>, key: string): { rows: T[] | null; error: string | null } {
  const path = useLocation().pathname;
  const [rows, setRows] = useState<T[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const read = () =>
      load().then(
        (found) => {
          if (!live) return;
          setRows(found);
          setError(null);
        },
        (err: unknown) => live && setError(err instanceof Error ? err.message : String(err)),
      );
    read();
    const timer = window.setInterval(read, REFRESH_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
    // `load` is a new function each render; `key` names what it reads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, path]);
  return { rows, error };
}

// What to tell the operator when a history list could not be read.
export function readFailed(what: string, error: string): string {
  return `Could not read ${what}: ${error}. It tries again in 30 seconds; reload to try now.`;
}
