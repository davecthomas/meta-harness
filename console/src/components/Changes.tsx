import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Group, NavLink, Paper, ScrollArea, SegmentedControl, Stack, Text, Textarea } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { api, type PullDiff } from "../api";
import { ErrorAlert } from "./ui";

// One line of a file's patch, with its line numbers on the old and new side.
export interface PatchLine {
  kind: "hunk" | "add" | "del" | "ctx";
  text: string;
  old: number | null;
  new: number | null;
}

// A GitHub patch's lines, numbered from each hunk's header (@@ -a,b +c,d @@).
export function parsePatch(patch: string | null): PatchLine[] {
  if (!patch) return [];
  const out: PatchLine[] = [];
  let oldNo = 0;
  let newNo = 0;
  for (const raw of patch.split("\n")) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw);
    if (hunk) {
      oldNo = Number(hunk[1]);
      newNo = Number(hunk[2]);
      out.push({ kind: "hunk", text: raw, old: null, new: null });
    } else if (raw.startsWith("+")) out.push({ kind: "add", text: raw.slice(1), old: null, new: newNo++ });
    else if (raw.startsWith("-")) out.push({ kind: "del", text: raw.slice(1), old: oldNo++, new: null });
    else if (!raw.startsWith("\\")) out.push({ kind: "ctx", text: raw.slice(1), old: oldNo++, new: newNo++ });
  }
  return out;
}

const COLORS = { add: "var(--mantine-color-teal-light)", del: "var(--mantine-color-red-light)", hunk: "var(--mantine-color-blue-light)", ctx: "transparent" };

function Num({ n }: { n: number | null }) {
  return <span style={{ display: "inline-block", width: 44, textAlign: "right", paddingRight: 8, color: "var(--mantine-color-dimmed)", userSelect: "none" }}>{n ?? ""}</span>;
}

// The files a task's pull request changes, a side-by-side or unified diff of the one picked, and comments on
// the lines picked: a click picks a line, a shift-click a range. [ and ] move between files.
export function Changes({ taskId }: { taskId: string }) {
  const [diff, setDiff] = useState<PullDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [at, setAt] = useState(0);
  const [split, setSplit] = useState(false);
  const [range, setRange] = useState<[number, number] | null>(null);
  const [comment, setComment] = useState("");
  const [posting, setPosting] = useState(false);

  useEffect(() => {
    api.pullDiff(taskId).then(setDiff, (err) => setError(err instanceof Error ? err.message : String(err)));
  }, [taskId]);

  const files = diff?.files ?? [];
  const file = files[at];
  const lines = useMemo(() => parsePatch(file?.patch ?? null), [file]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "[") setAt((i) => Math.max(0, i - 1));
      if (e.key === "]") setAt((i) => Math.min(files.length - 1, i + 1));
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [files.length]);

  useEffect(() => {
    setRange(null);
    setComment("");
  }, [at]);

  function pick(n: number | null, extend: boolean) {
    if (n === null) return;
    setRange((r) => (extend && r ? [Math.min(r[0], n), Math.max(r[1], n)] : [n, n]));
  }

  async function post() {
    if (!range || !file) return;
    setPosting(true);
    try {
      await api.commentOnLines(taskId, { path: file.path, line: range[1], start_line: range[0] < range[1] ? range[0] : null, body: comment });
      notifications.show({ color: "teal", title: "Comment posted", message: `${file.path}:${range[0]}${range[1] > range[0] ? `-${range[1]}` : ""}` });
      setRange(null);
      setComment("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPosting(false);
    }
  }

  if (error && !diff) return <ErrorAlert error={error} />;
  if (!diff) return <Text size="sm" c="dimmed">Reading the pull request…</Text>;
  const inRange = (n: number | null) => n !== null && range !== null && n >= range[0] && n <= range[1];
  const left = lines.filter((l) => l.kind !== "add");
  const right = lines.filter((l) => l.kind !== "del");

  const row = (l: PatchLine, i: number, side: "old" | "new" | "both") => {
    const n = side === "old" ? l.old : l.new;
    const commentable = side !== "old" && l.new !== null;
    return (
      <div
        key={`${side}-${i}`}
        data-testid={l.new !== null && side !== "old" ? `line-${l.new}` : undefined}
        onClick={(e) => commentable && pick(l.new, e.shiftKey)}
        style={{
          background: inRange(l.new) && side !== "old" ? "var(--mantine-color-yellow-light)" : COLORS[l.kind],
          whiteSpace: "pre",
          cursor: commentable ? "pointer" : "default",
        }}
      >
        {side === "both" ? <><Num n={l.old} /><Num n={l.new} /></> : <Num n={n} />}
        {l.kind === "add" ? "+" : l.kind === "del" ? "-" : " "}
        {l.text}
      </div>
    );
  };

  return (
    <Stack gap="sm">
      <ErrorAlert error={error} />
      <Group justify="space-between">
        <Text size="sm" c="dimmed">{files.length} files · {files.reduce((s, f) => s + f.additions, 0)} added · {files.reduce((s, f) => s + f.deletions, 0)} removed</Text>
        <SegmentedControl size="xs" value={split ? "split" : "unified"} onChange={(v) => setSplit(v === "split")} data={[{ value: "unified", label: "Unified" }, { value: "split", label: "Split" }]} />
      </Group>
      <Group align="flex-start" gap="sm" wrap="nowrap">
        <Paper withBorder p={4} w={260} style={{ flexShrink: 0 }}>
          <ScrollArea.Autosize mah="60vh">
            {files.map((f, i) => (
              <NavLink
                key={f.path}
                active={i === at}
                onClick={() => setAt(i)}
                label={<Text size="xs" ff="monospace" truncate>{f.path}</Text>}
                rightSection={<Group gap={4} wrap="nowrap"><Badge size="xs" color="teal" variant="light">+{f.additions}</Badge><Badge size="xs" color="red" variant="light">-{f.deletions}</Badge></Group>}
              />
            ))}
          </ScrollArea.Autosize>
        </Paper>
        <Stack gap="xs" style={{ flex: 1, minWidth: 0 }}>
          {file && <Text size="sm" ff="monospace" fw={600}>{file.path}</Text>}
          {file && !file.patch && <Text size="sm" c="dimmed">No text diff for this file (binary, or too large for GitHub to show).</Text>}
          <Paper withBorder p={0} style={{ overflow: "auto", maxHeight: "60vh", fontFamily: "var(--mantine-font-family-monospace)", fontSize: 12 }}>
            {split ? (
              <Group gap={0} align="flex-start" wrap="nowrap">
                <div style={{ flex: 1, minWidth: 0 }}>{left.map((l, i) => row(l, i, "old"))}</div>
                <div style={{ flex: 1, minWidth: 0, borderLeft: "1px solid var(--mantine-color-default-border)" }}>{right.map((l, i) => row(l, i, "new"))}</div>
              </Group>
            ) : (
              lines.map((l, i) => row(l, i, "both"))
            )}
          </Paper>
          {range && (
            <Paper withBorder p="sm">
              <Stack gap="xs">
                <Text size="xs" c="dimmed">Comment on {file.path}, line{range[1] > range[0] ? `s ${range[0]}-${range[1]}` : ` ${range[0]}`}. It posts to the pull request with your name.</Text>
                <Textarea aria-label="Comment" autosize minRows={2} value={comment} onChange={(e) => setComment(e.currentTarget.value)} />
                <Group justify="flex-end">
                  <Button size="xs" variant="default" onClick={() => setRange(null)}>Cancel</Button>
                  <Button size="xs" disabled={!comment.trim()} loading={posting} onClick={() => void post()}>Post comment</Button>
                </Group>
              </Stack>
            </Paper>
          )}
        </Stack>
      </Group>
    </Stack>
  );
}
