import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { MIN_CHARS, useSimilarGoals, WAIT_MS, type Similar } from "./SimilarGoals";

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
const CLOSE = { goal_id: "g-1", title: "Keep task through context compaction", status: "planning", since: "2026-11-22T12:00:00Z", score: 0.912 };
const NEAR = { goal_id: "g-2", title: "Cheaper compaction summaries", status: "active", since: "2026-11-20T12:00:00Z", score: 0.78 };
const LONG = "Keep the task through context compaction so a long run does not forget it.";

let sent: { url: string; body: Record<string, unknown> }[] = [];
let found: unknown = { goals: [CLOSE, NEAR], checked: true };
let status = 200;
let hook: Similar;

function Form({ description }: { description: string }) {
  hook = useSimilarGoals(description);
  return <>{hook.panel}</>;
}
const show = (description: string) => render(<MantineProvider theme={theme}><Form description={description} /></MantineProvider>);
const settle = () => act(async () => vi.advanceTimersByTimeAsync(WAIT_MS + 50));

beforeEach(() => {
  sent = [];
  found = { goals: [CLOSE, NEAR], checked: true };
  status = 200;
  vi.useFakeTimers();
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    sent.push({ url: String(url), body: JSON.parse(String(init?.body ?? "{}")) });
    return String(url).endsWith("/answer") ? json({ recorded: "x" }, 201) : json(status === 200 ? found : { detail: "the HTTP Gateway is unreachable" }, status);
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

test("a short description is not compared, and nothing shows when nothing is similar", async () => {
  show("x".repeat(MIN_CHARS - 1));
  await settle();
  expect(sent).toEqual([]);
  cleanup();
  found = { goals: [], checked: true };
  show(LONG);
  await settle();
  expect(sent.map((s) => s.url)).toEqual(["/v1/goals/similar"]);
  expect(screen.queryByTestId("similar-goals")).toBeNull();
});

test("similar open goals show closest first, each with its percentage and its two actions", async () => {
  show(LONG);
  await settle();
  const rows = screen.getAllByTestId("similar-goal");
  expect(rows.map((row) => row.textContent)).toEqual([expect.stringContaining("91% similar"), expect.stringContaining("78% similar")]);
  expect(rows[0].textContent).toContain("Keep task through context compaction");
  // Opening goes to the goal in a new tab, so the form keeps what was typed, and is recorded.
  const open = screen.getAllByRole("link", { name: "Open it" })[0];
  expect(open.getAttribute("href")).toBe("/goals/g-1");
  expect(open.getAttribute("target")).toBe("_blank");
  fireEvent.click(open);
  // Saying a row is not a duplicate records it and takes the row away.
  fireEvent.click(screen.getAllByRole("button", { name: "Not a duplicate" })[1]);
  expect(screen.getAllByTestId("similar-goal")).toHaveLength(1);
  const answers = sent.filter((s) => s.url.endsWith("/answer")).map((s) => [s.body.goal_id, s.body.answer, s.body.score]);
  expect(answers).toEqual([["g-1", "opened", 0.912], ["g-2", "not_duplicate", 0.78]]);
});

test("a save with a row showing asks once whether it is the same work, and different saves with the answer recorded", async () => {
  show(LONG);
  await settle();
  let go: boolean | null = null;
  await act(async () => void hook.beforeSave().then((answer) => (go = answer)));
  expect(screen.getByTestId("similar-question").textContent).toContain('Is this the same work as "Keep task through context compaction"?');
  expect(go).toBeNull();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "No, different" })));
  expect(go).toBe(true);
  act(() => hook.saved("g-new"));
  expect(sent.filter((s) => s.url.endsWith("/answer")).map((s) => s.body)).toEqual([{ goal_id: "g-1", score: 0.912, answer: "different", new_goal_id: "g-new" }]);
  // Asked once: a second save goes straight on.
  let again: boolean | null = null;
  await act(async () => void hook.beforeSave().then((answer) => (again = answer)));
  expect(again).toBe(true);
});

test("same work offers that goal or this one anyway, and merges nothing", async () => {
  show(LONG);
  await settle();
  let go: boolean | null = null;
  await act(async () => void hook.beforeSave().then((answer) => (go = answer)));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Yes, same work" })));
  expect(go).toBeNull();
  expect(sent.filter((s) => s.url.endsWith("/answer")).map((s) => s.body.answer)).toEqual(["same_work"]);
  expect(screen.getByRole("link", { name: "Open that goal" }).getAttribute("href")).toBe("/goals/g-1");
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Create this one anyway" })));
  expect(go).toBe(true);
});

test("a check that cannot run says why and that the goal can still be saved, and a save asks nothing", async () => {
  status = 502;
  show(LONG);
  await settle();
  expect(screen.getByTestId("similar-failed").textContent).toBe("Could not look for similar goals: the HTTP Gateway is unreachable. You can still save.");
  let go: boolean | null = null;
  await act(async () => void hook.beforeSave().then((answer) => (go = answer)));
  expect(go).toBe(true);
});
