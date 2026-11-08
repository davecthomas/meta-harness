import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useEventStream } from "./hooks";

// The browser's EventSource, as the hook sees it: each instance records its URL, and the test plays the server.
class FakeEventSource {
  static CLOSED = 2;
  static opened: FakeEventSource[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners: Record<string, ((m: MessageEvent) => void)[]> = {};
  constructor(public url: string) {
    FakeEventSource.opened.push(this);
  }
  addEventListener(type: string, listener: (m: MessageEvent) => void) {
    (this.listeners[type] ??= []).push(listener);
  }
  emit(position: number) {
    const message = new MessageEvent("event", { data: JSON.stringify({ position }), lastEventId: String(position) });
    this.listeners.event?.forEach((l) => l(message));
  }
  refuse() {
    this.readyState = FakeEventSource.CLOSED;
    this.onerror?.();
  }
  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  FakeEventSource.opened = [];
});

test("the stream opens on the edge's address with a ticket, and reopens from the last event with a new one", async () => {
  let tickets = 0;
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("fetch", () => {
    tickets += 1;
    const body = { base: "https://edge.run.app", ticket: `ticket-${tickets}` };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  });
  const seen: unknown[] = [];

  const { result } = renderHook(() => useEventStream(["t-1"], 10, (e) => seen.push(e)));

  await waitFor(() => expect(FakeEventSource.opened.length).toBe(1));
  const first = FakeEventSource.opened[0];
  expect(first.url).toBe("https://edge.run.app/v1/events/tail?task_id=t-1&from_position=10&ticket=ticket-1");
  act(() => {
    first.onopen?.();
    first.emit(11);
    first.emit(12);
  });
  expect(result.current).toBe("live");
  expect(seen).toEqual([{ position: 11 }, { position: 12 }]);

  vi.useFakeTimers();
  act(() => first.refuse());
  expect(result.current).toBe("reconnecting");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  vi.useRealTimers();

  await waitFor(() => expect(FakeEventSource.opened.length).toBe(2));
  expect(FakeEventSource.opened[1].url).toBe(
    "https://edge.run.app/v1/events/tail?task_id=t-1&from_position=12&ticket=ticket-2",
  );
});
