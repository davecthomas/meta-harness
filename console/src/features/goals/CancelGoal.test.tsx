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
    stopped: ["t-running"],
    left: ["t-published", "t-ready"],
  });
});

test("Cancel goal asks first, says what happens to the tasks, and links those it leaves", async () => {
  const onCancel = vi.fn(() => Promise.resolve());
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
  expect(dialog.textContent).toContain("2 tasks that have not started are canceled, and 1 running task is stopped");
  expect(dialog.textContent).toContain("2 tasks have a pull request open and are left as they are");
  expect(screen.getByRole("link", { name: "t-published" }).getAttribute("href")).toBe("/tasks/t-published");
  expect(screen.getByRole("link", { name: "t-ready" }).getAttribute("href")).toBe("/tasks/t-ready");

  fireEvent.click(screen.getAllByRole("button", { name: "Cancel goal" }).at(-1)!);
  await waitFor(() => expect(onCancel).toHaveBeenCalledTimes(1));
});
