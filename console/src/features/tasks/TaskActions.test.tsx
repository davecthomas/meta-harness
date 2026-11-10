import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import type { Task } from "../../api";
import { theme } from "../../theme";
import { describe, TaskActions } from "./TaskActions";

afterEach(() => vi.unstubAllGlobals());

const TASK = { id: "t-1", title: "Fix it", status: "review_unresolved", pr_url: "https://github.com/o/r/pull/7", max_usd: "10.00" } as Task;

function show(task: Task, merged = false) {
  const asked: { url: string; body: any }[] = [];
  const changed: Task[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    asked.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    return Promise.resolve(new Response(JSON.stringify({ ...task, status: "queued" }), { status: 202, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <TaskActions task={task} status={task.status} merged={merged} onChanged={(t) => changed.push(t)} onError={() => undefined} />
    </MantineProvider>,
  );
  return { asked, changed };
}

test("an unresolved task offers Retry, Merge and Cancel, and no Clear", () => {
  show(TASK);
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Merge" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Cancel task" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Clear/ })).toBeNull();
});

test("Retry says what it will do, takes a spend limit and sends the task back", async () => {
  const { asked, changed } = show(TASK);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  const dialog = await screen.findByRole("dialog", { name: "Retry the task" });
  expect(dialog.textContent).toContain("Its pull request goes through checks, review and fixes again");
  fireEvent.change(within(dialog).getByLabelText(/Spend limit/), { target: { value: "15" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(changed).toHaveLength(1));
  expect(asked[0]).toEqual({ url: "/v1/tasks/t-1/retry", body: { max_usd: 15 } });
});

test("Merge asks for the merge, and a merged pull request offers Mark done in place of Cancel and Merge", async () => {
  const { asked } = show({ ...TASK, status: "ready" });
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Merge" }));
  await waitFor(() => expect(asked).toHaveLength(1));
  expect(asked[0].url).toBe("/v1/tasks/t-1/merge");
});

test("a task whose pull request merged is marked done, and a failed one with no pull request only retries", () => {
  const { unmount } = render(<div />);
  unmount();
  show({ ...TASK, status: "ready" }, true);
  expect(screen.getByRole("button", { name: "Mark done" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Merge" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Cancel task" })).toBeNull();
});

test("each action's description says what happens to the task and to the pull request", () => {
  expect(describe("retry", { ...TASK, pr_url: null })).toContain("new first run");
  expect(describe("cancel", TASK)).toContain("stays open on GitHub");
  expect(describe("merge", TASK)).toContain("merge queue");
  expect(describe("done", TASK)).toContain("closed");
});
