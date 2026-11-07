import { useCallback, useEffect, useRef, useState } from "react";
import type { MhEvent, Page } from "./api";
import { tailUrl } from "./api";

// A list that reads ahead: it loads the first page, then the next whenever the sentinel element
// (put it after the last item) comes within a screen of view.
export function useInfinite<T>(load: (offset: number, limit: number) => Promise<Page<T>>, pageSize: number, key: string) {
  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loading = useRef(false);
  const sentinel = useRef<HTMLDivElement | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  const itemsRef = useRef(items);
  itemsRef.current = items;

  const more = useCallback(async (reset = false) => {
    if (loading.current) return;
    loading.current = true;
    try {
      const offset = reset ? 0 : itemsRef.current.length;
      const page = await loadRef.current(offset, pageSize);
      setItems((current) => (reset ? page.items : [...current, ...page.items]));
      setTotal(page.total);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      loading.current = false;
    }
  }, [pageSize]);

  useEffect(() => {
    setItems([]);
    setTotal(null);
    itemsRef.current = [];
    void more(true);
  }, [key, more]);

  useEffect(() => {
    const node = sentinel.current;
    if (!node) return;
    const observer = new IntersectionObserver((entries) => {
      const done = total !== null && itemsRef.current.length >= total;
      if (entries.some((e) => e.isIntersecting) && !done) void more();
    }, { rootMargin: "100% 0px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, [more, total, items.length]);

  const reload = useCallback(() => void more(true), [more]);
  return { items, total, error, sentinel, reload, done: total !== null && items.length >= total };
}

// One server-sent-event stream for a set of tasks. A new set of ids opens a new stream from `from`.
export function useEventStream(taskIds: string[], from: number | null, onEvent: (e: MhEvent) => void) {
  const [state, setState] = useState<"idle" | "connecting" | "live" | "reconnecting">("idle");
  const handler = useRef(onEvent);
  handler.current = onEvent;
  const key = taskIds.join(",");

  useEffect(() => {
    if (!key || from === null) {
      setState("idle");
      return;
    }
    setState("connecting");
    const stream = new EventSource(tailUrl(key.split(","), from));
    stream.addEventListener("event", (message) => handler.current(JSON.parse((message as MessageEvent).data)));
    stream.onopen = () => setState("live");
    stream.onerror = () => setState("reconnecting");
    return () => stream.close();
  }, [key, from]);

  return state;
}

// The value, settled: it changes only after `ms` with no further change.
export function useSettled<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}
