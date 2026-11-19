import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Anchor, Badge, Group, Paper, Text } from "@mantine/core";
import { api, type MhEvent, type Task } from "../api";
import { statusColor } from "../theme";
import { statusLabel } from "./status";
import { useEventStream, useSettled } from "../hooks";
import { PrLink } from "./ui";
import { describe, fold, taskOf, type Line, type TileFacts } from "../terminal";
import { Terminal } from "./Terminal";
import { usd } from "../money";

export type TileSize = 1 | 4 | 16 | 32;
const KEEP = 300;
const BACKLOG: Record<TileSize, number> = { 1: 200, 4: 80, 16: 40, 32: 30 };
const EMPTY: TileFacts = { turn: 0, usd: 0, model: "", status: null };

// Every `run_prompt` of each task, oldest first. A failed read shows the tiles without them rather
// than holding the tiles back.
async function runPrompts(ids: string[]): Promise<Map<string, MhEvent[]>> {
  const found = new Map<string, MhEvent[]>();
  try {
    for (const e of await api.taskEvents(ids, ["run_prompt"])) {
      const id = taskOf(e);
      if (e.event_type !== "run_prompt" || !id) continue;
      found.set(id, [...(found.get(id) ?? []), e]);
    }
  } catch {
    // The tiles' own events still show.
  }
  return found;
}

const isPrompt = (e: MhEvent) => e.event_type === "run_prompt";

// A tile's line for an event, knowing its run, so a run's prompt can follow the run's output.
function lineOf(e: MhEvent): Line | null {
  const line = describe(e);
  if (!line) return null;
  const run = e.run_id ?? (typeof e.payload?.run_id === "string" ? e.payload.run_id : null);
  return { ...line, run };
}

// What a tile shows: its newest output, headed by the prompt of each run that output belongs to,
// and always the newest run's prompt. The prompts are kept apart from the trimmed output, so a
// long run's prompt stays at the top of the tile however far its output runs on.
function tileLines(output: Line[], prompts: Line[]): Line[] {
  const runs = new Set(output.map((l) => l.run).filter((r): r is string => !!r));
  const first = output[0]?.position ?? Number.MAX_SAFE_INTEGER;
  const newest = prompts[prompts.length - 1];
  const shown = prompts.filter((p) => p === newest || (p.run && runs.has(p.run)) || p.position >= first);
  return [...shown, ...output].sort((a, b) => a.position - b.position);
}

// Terminal tiles for running tasks. Only tiles on screen stream: the grid keeps one event stream
// open for exactly the visible tasks, reopened half a second after scrolling stops. A tile that
// scrolls into view first loads its recent events and its runs' prompts, then joins the stream; a
// tile off screen keeps what it showed. Each tile drops events it has already shown, by position.
export function TileGrid(props: { tasks: Task[]; size: TileSize; onStreamState?: (s: string) => void }) {
  const [visible, setVisible] = useState<string[]>([]);
  const [lines, setLines] = useState<Record<string, Line[]>>({});
  const [facts, setFacts] = useState<Record<string, TileFacts>>({});
  const [from, setFrom] = useState<number | null>(null);
  const lastPosition = useRef<Record<string, number>>({});
  // Each tile's newest output lines and its runs' prompt lines, which tileLines puts together.
  const output = useRef<Record<string, Line[]>>({});
  const prompts = useRef<Record<string, Line[]>>({});
  const streaming = useRef<Set<string>>(new Set());
  const observer = useRef<IntersectionObserver | null>(null);
  const shown = useRef<Set<string>>(new Set());

  // React attaches each tile (calling `watch`) before effects run, so the observer is made on first use.
  const visibility = useCallback(() => {
    if (!observer.current) {
      observer.current = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          const id = (entry.target as HTMLElement).dataset.taskId!;
          if (entry.isIntersecting) shown.current.add(id);
          else shown.current.delete(id);
        }
        setVisible([...shown.current].sort());
      });
    }
    return observer.current;
  }, []);

  useEffect(() => () => observer.current?.disconnect(), []);

  const watch = useCallback((node: HTMLElement | null) => {
    if (node) visibility().observe(node);
  }, [visibility]);

  const settled = useSettled(visible, 500);

  // When the visible set settles: backfill tiles that just came into view, then stream them all.
  useEffect(() => {
    let stale = false;
    const fresh = settled.filter((id) => !streaming.current.has(id));
    streaming.current = new Set(settled);
    if (!settled.length) {
      setFrom(null);
      return;
    }
    (async () => {
      const backlog = BACKLOG[props.size];
      // A long run's backlog no longer reaches back to its prompt, so the prompts are read apart.
      const read = fresh.length ? runPrompts(fresh) : Promise.resolve(new Map<string, MhEvent[]>());
      await Promise.all(
        fresh.map(async (id) => {
          const [page, found] = await Promise.all([api.events([id], backlog), read]);
          if (stale) return;
          const byPosition = new Map<number, MhEvent>();
          for (const e of [...(found.get(id) ?? []), ...page.events.filter(isPrompt)]) byPosition.set(e.position, e);
          prompts.current[id] = [...byPosition.values()]
            .sort((a, b) => a.position - b.position)
            .map(lineOf)
            .filter((l): l is Line => l !== null);
          output.current[id] = page.events
            .filter((e) => !isPrompt(e))
            .map(lineOf)
            .filter((l): l is Line => l !== null)
            .slice(-KEEP);
          setLines((current) => ({ ...current, [id]: tileLines(output.current[id], prompts.current[id]) }));
          setFacts((current) => ({ ...current, [id]: page.events.reduce(fold, EMPTY) }));
          const newest = page.events.length ? page.events[page.events.length - 1].position : 0;
          lastPosition.current[id] = Math.max(newest, page.latest_position);
        }),
      );
      if (stale) return;
      setFrom(Math.min(...settled.map((id) => lastPosition.current[id] ?? 0)));
    })();
    return () => {
      stale = true;
    };
  }, [settled, props.size]);

  const onEvent = useCallback((e: MhEvent) => {
    const id = taskOf(e);
    if (!id || e.position <= (lastPosition.current[id] ?? 0)) return;
    lastPosition.current[id] = e.position;
    setFacts((current) => ({ ...current, [id]: fold(current[id] ?? EMPTY, e) }));
    const line = lineOf(e);
    if (!line) return;
    if (isPrompt(e)) prompts.current[id] = [...(prompts.current[id] ?? []), line];
    else output.current[id] = [...(output.current[id] ?? []), line].slice(-KEEP);
    const next = tileLines(output.current[id] ?? [], prompts.current[id] ?? []);
    setLines((current) => ({ ...current, [id]: next }));
  }, []);

  const streamIds = useMemo(() => (from === null ? [] : settled), [settled, from]);
  const state = useEventStream(streamIds, from, onEvent);
  const report = props.onStreamState;
  useEffect(() => report?.(state), [state, report]);

  return (
    <div className={`tiles up-${props.size}`} data-visible={visible.length} data-streaming={settled.length}>
      {props.tasks.map((t) => {
        const f = facts[t.id] ?? EMPTY;
        const status = f.status ?? t.status;
        return (
          <Paper component="section" className="tile" p={0} key={t.id} data-task-id={t.id} ref={watch}>
            <header>
              <Anchor component={Link} to={`/tasks/${t.id}`} className="title" fw={600} size="sm" truncate="end">{t.title}</Anchor>
              <Group className="facts" gap={8} wrap="wrap">
                <Badge size="xs" variant="light" color={statusColor(status)} tt="none">{statusLabel(status)}</Badge>
                {props.size !== 32 && <Text size="xs" c="dimmed" ff="monospace">{t.repo.split("/").pop()}</Text>}
                {t.pr_url && <PrLink url={t.pr_url} size="xs" />}
                <Text size="xs" c="dimmed">turn {f.turn}</Text>
                <Text size="xs" c="dimmed">{usd(f.usd)}</Text>
                {props.size <= 4 && f.model && <Text size="xs" c="dimmed" ff="monospace">{f.model}</Text>}
              </Group>
            </header>
            <Terminal lines={lines[t.id] ?? []} />
          </Paper>
        );
      })}
    </div>
  );
}
