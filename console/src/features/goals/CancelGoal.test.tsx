import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { CancelGoal, cancelEffect } from "./CancelGoal";

const TASKS = [
  { id: "t-queued", status: "queued", pr_url: null },
  { id: "t-blocked", status: "blocked", pr_url: null },
  { id: "t-running", status: "running", pr_url: null },
  { id: "t-published", status: "running", pr_url: "https://github.com/o/r/pull/1" },
  { id: "t-ready", status: "ready", pr_url: "https://github.com/o/r/pull/2" },
  { id: "t-closed", status: "closed", pr_url: "https://github.com/o/r/pull/3" },
];

test("canceling a goal cancels unstarted tasks, stops running ones, and leaves those with a pull request open", () => {
  expect(cancelEffect(TASKS)).toEqual({
    canceled: ["t-queued", "t-blocked"],
    stopped: ["t-running", "t-published"],
    left: ["t-ready"],
  });
});

afterEach(() => vi.unstubAllGlobals());

test("Cancel goal asks first, says what happens to the tasks, and links those it leaves", async () => {
  const onCancel = vi.fn(() => Promise.resolve());
  // Each task left is linked by its name, read from the Task API.
  const names: Record<string, string> = { "t-published": "Profile image picker", "t-ready": "Hint text contrast" };
  vi.stubGlobal("fetch", (url: string) => {
    const id = String(url).split("/").pop()!;
    return Promise.resolve(new Response(JSON.stringify({ id, title: "A long title", name: names[id] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <CancelGoal tasks={TASKS} onCancel={onCancel} />
      </MemoryRouter>
    </MantineProvider>,
  );

  fireEvent.click(screen.getByRole("button", { name: "Cancel goal" }));

  const dialog = await screen.findByTestId("cancel-goal");
  expect(onCancel).not.toHaveBeenCalled();
  expect(dialog.textContent).toContain("2 tasks that have not started are canceled. 2 running tasks are stopped.");
  expect(dialog.textContent).toContain("1 task has a pull request open and is left as it is. Nothing more is asked of you for it");
  expect((await screen.findByRole("link", { name: "Hint text contrast" })).getAttribute("href")).toBe("/tasks/t-ready");

  fireEvent.click(screen.getAllByRole("button", { name: "Cancel goal" }).at(-1)!);
  await waitFor(() => expect(onCancel).toHaveBeenCalledTimes(1));
});

test("a goal with no tasks to cancel or stop says only that it takes in no more issues", async () => {
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <CancelGoal tasks={[]} onCancel={vi.fn(() => Promise.resolve())} />
      </MemoryRouter>
    </MantineProvider>,
  );

  fireEvent.click(screen.getByRole("button", { name: "Cancel goal" }));

  const dialog = await screen.findByTestId("cancel-goal");
  expect(dialog.textContent).toContain("The goal takes in no more issues.");
  expect(dialog.textContent).not.toMatch(/\b0 tasks?\b/);
  expect(dialog.textContent).not.toContain("Reopening the goal");
});
