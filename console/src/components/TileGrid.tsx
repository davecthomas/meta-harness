import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type MhEvent, type Task } from "../api";
import { useEventStream, useSettled } from "../hooks";
import { describe, fold, taskOf, type Line, type TileFacts } from "../terminal";
import { Terminal } from "./Terminal";

export type TileSize = 4 | 16 | 32;
const KEEP = 300;
const BACKLOG: Record<TileSize, number> = { 4: 80, 16: 40, 32: 30 };
const EMPTY: TileFacts = { turn: 0, usd: 0, model: "", status: null };

// Terminal tiles for running tasks. Only tiles on screen stream: the grid keeps one event stream
// open for exactly the visible tasks, reopened half a second after scrolling stops. A tile that
// scrolls into view first loads its recent events, then joins the stream; a tile off screen keeps
// what it showed. Each tile drops events it has already shown, by position.
export function TileGrid(props: { tasks: Task[]; size: TileSize; onStreamState?: (s: string) => void }) {
  const [visible, setVisible] = useState<string[]>([]);
  const [lines, setLines] = useState<Record<string, Line[]>>({});
  const [facts, setFacts] = useState<Record<string, TileFacts>>({});
  const [from, setFrom] = useState<number | null>(null);
  const lastPosition = useRef<Record<string, number>>({});
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
      await Promise.all(
        fresh.map(async (id) => {
          const page = await api.events([id], backlog);
          if (stale) return;
          const shownLines = page.events.map(describe).filter((l): l is Line => l !== null);
          setLines((current) => ({ ...current, [id]: shownLines.slice(-KEEP) }));
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
    const line = describe(e);
    if (line) setLines((current) => ({ ...current, [id]: [...(current[id] ?? []), line].slice(-KEEP) }));
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
          <section className="tile" key={t.id} data-task-id={t.id} ref={watch}>
            <header>
              <div className="title"><Link to={`/tasks/${t.id}`}>{t.title}</Link></div>
              <div className="facts">
                <span className={`status-${status}`}>{status}</span>
                <span className="mono">{t.id}</span>
                {props.size !== 32 && <span className="mono">{t.repo.split("/").pop()}</span>}
                <span>turn {f.turn}</span>
                <span>${f.usd.toFixed(4)}</span>
                {props.size === 4 && f.model && <span className="mono">{f.model}</span>}
              </div>
            </header>
            <Terminal lines={lines[t.id] ?? []} />
          </section>
        );
      })}
    </div>
  );
}
