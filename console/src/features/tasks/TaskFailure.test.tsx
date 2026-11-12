import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { expect, test } from "vitest";
import type { MhEvent, Task } from "../../api";
import { theme } from "../../theme";
import { TaskFailure } from "./TaskFailure";

const TASK = { id: "t-1", title: "Fix it", status: "failed", status_reason: "deploy failed on the merge commit" } as Task;
const EVENTS = [
  { position: 1, event_type: "attempt_finished", run_id: "t-1:run-1", payload: { attempt: 1, tier: "standard", candidate: "harness:a/b:c", status: "FINISHED", detail: "ended FINISHED" } },
  { position: 2, event_type: "attempt_finished", run_id: "t-1:run-7", payload: { attempt: 7, tier: "fix", candidate: "harness:a/b:c", status: "ERROR", detail: "ended ERROR (spend_limit)" } },
] as unknown as MhEvent[];

function show(status: string) {
  return render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <TaskFailure task={{ ...TASK, status }} status={status} events={EVENTS} />
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("a failed task says why at the top, names each run that did not finish, and links its log lines", () => {
  show("failed");
  const alert = screen.getByRole("alert");
  expect(alert.textContent).toContain("This task failed");
  expect(alert.textContent).toContain("The control plane recorded: deploy failed on the merge commit");
  expect(alert.textContent).toContain("What to do: Open the task for its runs and their logs. Retry it once the cause is fixed.");
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  expect(alert.textContent).toContain("(fix) on harness:a/b:c: ended ERROR (spend_limit)");
  expect(alert.textContent).not.toContain("ended FINISHED");
  expect(screen.getByRole("link", { name: "Run 7" }).getAttribute("href")).toBe("/tasks/t-1?run=t-1%3Arun-7");
  expect(screen.getByRole("link", { name: /Warnings and errors/ }).getAttribute("href")).toBe("/admin/logs?q=t-1&minutes=1440");
});

test("an unresolved task is marked too, and a task in any other status shows nothing", () => {
  const { unmount } = show("review_unresolved");
  expect(screen.getByRole("alert").textContent).toContain("The review loop ended without a clean review");
  unmount();
  show("running");
  expect(screen.queryByRole("alert")).toBeNull();
});
