import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Group, Kbd, Modal, Stack, Table, Text, TextInput, UnstyledButton } from "@mantine/core";
import { IconSearch } from "@tabler/icons-react";
import { api } from "../api";

// One thing the palette can do: open a page, a goal or a task, or run an action.
export interface Command {
  group: "Pages" | "Actions" | "Goals" | "Tasks";
  label: string;
  detail?: string;
  run: () => void | Promise<void>;
}

// Keyboard shortcuts, shown by ?.
export const SHORTCUTS: [string, string][] = [
  ["⌘K / Ctrl+K", "Open the command palette"],
  ["g o", "Go to the Overview"],
  ["g g", "Go to Goals"],
  ["g t", "Go to Tasks"],
  ["n", "New goal"],
  ["j / k", "Move down or up a list"],
  ["Enter", "Open the row picked in a list"],
  ["?", "Show these shortcuts"],
];

function typing(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || (target instanceof HTMLElement && target.isContentEditable);
}

// The command palette (⌘K or Ctrl+K), the go-to shortcuts, and ? for the list of them.
export function CommandPalette({ admin }: { admin: boolean }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [help, setHelp] = useState(false);
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Command[]>([]);
  const [at, setAt] = useState(0);
  const pending = useRef<string | null>(null);

  const go = (to: string) => () => {
    setOpen(false);
    navigate(to);
  };

  const fixed: Command[] = useMemo(
    () => [
      { group: "Pages", label: "Overview", run: go("/") },
      { group: "Pages", label: "Goals", run: go("/goals") },
      { group: "Pages", label: "Tasks", run: go("/tasks") },
      { group: "Pages", label: "Settings", run: go("/settings") },
      ...(admin
        ? ([
            { group: "Pages", label: "Configuration", run: go("/config") },
            { group: "Pages", label: "Repositories", run: go("/admin/repos") },
            { group: "Pages", label: "Logs", run: go("/admin/logs") },
          ] as Command[])
        : []),
      { group: "Actions", label: "New goal", run: go("/goals/new") },
      { group: "Actions", label: "Kill switch", detail: "stop everything, or lift it", run: go(admin ? "/admin/kill-switch" : "/") },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [admin],
  );

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
        return;
      }
      if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (pending.current === "g") {
        pending.current = null;
        const to = { o: "/", g: "/goals", t: "/tasks" }[e.key];
        if (to) navigate(to);
        return;
      }
      if (e.key === "g") pending.current = "g";
      else if (e.key === "n") navigate("/goals/new");
      else if (e.key === "?") setHelp(true);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [navigate]);

  // Goals and tasks matching the text, from the planner's search; pages and actions match by name.
  useEffect(() => {
    if (!open) return;
    const text = q.trim();
    if (!text) {
      setFound([]);
      return;
    }
    let stale = false;
    const wait = setTimeout(() => {
      api.search(text).then(
        (r) => {
          if (stale) return;
          setFound([
            ...r.goals.map((g) => ({ group: "Goals" as const, label: g.title, detail: g.status, run: go(`/goals/${g.id}`) })),
            ...r.tasks.map((t) => ({ group: "Tasks" as const, label: t.title, detail: t.status, run: go(`/tasks/${t.id}`) })),
          ]);
        },
        () => !stale && setFound([]),
      );
    }, 200);
    return () => {
      stale = true;
      clearTimeout(wait);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, open]);

  const text = q.trim().toLowerCase();
  const shown = [...fixed.filter((c) => !text || c.label.toLowerCase().includes(text)), ...found];
  useEffect(() => setAt(0), [q, found.length]);

  return (
    <>
      <Modal
        opened={open}
        onClose={() => setOpen(false)}
        withCloseButton={false}
        size="lg"
        yOffset="10vh"
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setAt((i) => Math.min(shown.length - 1, i + 1));
          if (e.key === "ArrowUp") setAt((i) => Math.max(0, i - 1));
          if (e.key === "Enter" && shown[at]) void shown[at].run();
        }}
      >
        <TextInput
          data-autofocus
          aria-label="Search the console"
          placeholder="Go to a page, find a goal or task, or run an action"
          leftSection={<IconSearch size={16} />}
          value={q}
          onChange={(e) => setQ(e.currentTarget.value)}
          mb="sm"
        />
        <Stack gap={2} data-testid="palette-results">
          {shown.map((c, i) => (
            <UnstyledButton
              key={`${c.group}-${c.label}-${i}`}
              onClick={() => void c.run()}
              onMouseEnter={() => setAt(i)}
              p={6}
              style={{ borderRadius: 6, background: i === at ? "var(--mantine-color-default-hover)" : undefined }}
            >
              <Group justify="space-between" wrap="nowrap">
                <Text size="sm" truncate>{c.label}</Text>
                <Group gap={6} wrap="nowrap">
                  {c.detail && <Text size="xs" c="dimmed">{c.detail}</Text>}
                  <Badge size="xs" variant="light">{c.group}</Badge>
                </Group>
              </Group>
            </UnstyledButton>
          ))}
          {shown.length === 0 && <Text size="sm" c="dimmed">Nothing matches.</Text>}
        </Stack>
      </Modal>
      <Modal opened={help} onClose={() => setHelp(false)} title="Keyboard shortcuts">
        <Table>
          <Table.Tbody>
            {SHORTCUTS.map(([keys, what]) => (
              <Table.Tr key={keys}>
                <Table.Td><Kbd>{keys}</Kbd></Table.Td>
                <Table.Td><Text size="sm">{what}</Text></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Modal>
    </>
  );
}

// j and k move through a list's rows and Enter opens the one picked. Returns the picked row's index.
export function useRowKeys(count: number, open: (index: number) => void): number | null {
  const [at, setAt] = useState<number | null>(null);
  const latest = useRef(open);
  latest.current = open;
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey || !count) return;
      if (e.key === "j") setAt((i) => (i === null ? 0 : Math.min(count - 1, i + 1)));
      if (e.key === "k") setAt((i) => (i === null ? 0 : Math.max(0, i - 1)));
      if (e.key === "Enter") setAt((i) => {
        if (i !== null) latest.current(i);
        return i;
      });
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [count]);
  return at;
}
