import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { GOAL_CHANGED, goalChanged, goalTailUrl, SETTLE_MS, useGoalChanged, useGoalStream } from "./goalSync";
import { PLAN_CHANGED } from "./needActions";

class FakeStream {
  static opened: FakeStream[] = [];
  listeners: Record<string, () => void> = {};
  readyState = 1;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeStream.opened.push(this);
  }
  addEventListener(name: string, heard: () => void) {
    this.listeners[name] = heard;
  }
  close() {
    this.readyState = 2;
  }
}

beforeEach(() => {
  FakeStream.opened = [];
  vi.useFakeTimers();
  vi.stubGlobal("EventSource", Object.assign(FakeStream, { CLOSED: 2 }));
  vi.stubGlobal("fetch", (url: string) =>
    Promise.resolve(new Response(JSON.stringify(String(url).includes("stream-ticket") ? { base: "https://edge.test", ticket: "t1" } : { needs: [] }), { status: 200, headers: { "Content-Type": "application/json" } })));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function Part({ goalId, onChange }: { goalId: string; onChange: () => void }) {
  useGoalChanged(goalId, onChange);
  return null;
}
function Page({ goalId }: { goalId: string }) {
  useGoalStream(goalId);
  return null;
}

test("an action on a goal tells every part of its page, the plan and what needs the operator, at once", () => {
  const heard: string[] = [];
  window.addEventListener(PLAN_CHANGED, () => heard.push("plan"));
  render(<><Part goalId="g-1" onChange={() => heard.push("a")} /><Part goalId="g-1" onChange={() => heard.push("b")} /><Part goalId="g-2" onChange={() => heard.push("other")} /></>);
  act(() => goalChanged("g-1"));
  // Both parts of g-1's page in the same moment; another goal's page is left alone.
  expect(heard).toEqual(["a", "b", "plan"]);
});

test("the goal's event stream says the goal changed, once for a burst of events", async () => {
  let changed = 0;
  window.addEventListener(GOAL_CHANGED, () => changed++);
  render(<Page goalId="g-1" />);
  await act(async () => vi.advanceTimersByTimeAsync(10));
  expect(FakeStream.opened.map((s) => s.url)).toEqual([goalTailUrl("g-1", "https://edge.test", "t1")]);
  expect(FakeStream.opened[0].url).toContain("goal_id=g-1");
  expect(FakeStream.opened[0].url).toContain("from_position=-1");
  act(() => {
    FakeStream.opened[0].listeners.event();
    FakeStream.opened[0].listeners.event();
    FakeStream.opened[0].listeners.event();
  });
  expect(changed).toBe(0);
  await act(async () => vi.advanceTimersByTimeAsync(SETTLE_MS + 10));
  expect(changed).toBe(1);
});

test("a stream that drops is opened again", async () => {
  render(<Page goalId="g-1" />);
  await act(async () => vi.advanceTimersByTimeAsync(10));
  act(() => {
    FakeStream.opened[0].readyState = 2;
    FakeStream.opened[0].onerror?.();
  });
  await act(async () => vi.advanceTimersByTimeAsync(2100));
  expect(FakeStream.opened).toHaveLength(2);
});

test("the work's stream tells every page something changed, and each goal it named that it changed", async () => {
  const { useWorkStream, useWorkChanged, WORK_EVENTS, workTailUrl } = await import("./goalSync");
  const heard: string[] = [];
  function Shell() {
    useWorkStream(true);
    useWorkChanged(() => heard.push("work"));
    return null;
  }
  window.addEventListener(GOAL_CHANGED, (event) => heard.push(`goal ${(event as CustomEvent<string>).detail}`));
  render(<Shell />);
  await act(async () => vi.advanceTimersByTimeAsync(10));
  const stream = FakeStream.opened[0];
  expect(stream.url).toBe(workTailUrl("https://edge.test", "t1"));
  // Only what changes a page's words is followed: a run's own chatter is not.
  expect(WORK_EVENTS).toContain("task_status_changed");
  expect(WORK_EVENTS).toContain("plan_approved");
  expect(WORK_EVENTS).not.toContain("model_call");
  const send = (data: unknown) => (stream.listeners.event as unknown as (m: { data: string }) => void)({ data: JSON.stringify(data) });
  act(() => {
    send({ event_type: "plan_approved", payload: { goal_id: "g-1" } });
    send({ event_type: "task_status_changed", tags: { goal_id: "g-1" }, payload: {} });
    send({ event_type: "task_status_changed", payload: {} });
  });
  await act(async () => vi.advanceTimersByTimeAsync(SETTLE_MS + 10));
  // One burst: the goal it named is told once, and every page is told once.
  expect(heard).toEqual(["goal g-1", "work"]);
});

test("nobody signed in opens no stream", async () => {
  const { useWorkStream } = await import("./goalSync");
  function Shell() {
    useWorkStream(false);
    return null;
  }
  render(<Shell />);
  await act(async () => vi.advanceTimersByTimeAsync(10));
  expect(FakeStream.opened).toHaveLength(0);
});
