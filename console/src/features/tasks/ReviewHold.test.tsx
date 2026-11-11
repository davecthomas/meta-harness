import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { ReviewRound, Task } from "../../api";
import { theme } from "../../theme";
import { Reviews } from "./Reviews";

afterEach(() => vi.unstubAllGlobals());

const TASK = {
  id: "t-1", title: "Fix", status: "running", waiting_on: "review", pr_url: "https://github.com/o/r/pull/9",
  status_reason: "review round 1 asked for changes (2 findings); waiting 30 minutes for an operator",
} as Task;
const ROUND: ReviewRound = {
  round: 1, verdict: "changes", reviewer: "harness:pi-review/x:y", head_sha: "abc1234ff", summary: "Two things.",
  findings: [
    { n: 1, path: "pyproject.toml", line: 3, body: "Revert the version bump.", dismissed: null },
    { n: 2, path: "app.py", line: 9, body: "Off by one.", dismissed: { by: "dave", reason: "Fixed already." } },
  ],
  review_url: null, reviewed_at: null, run_id: "t-1:run-2", next: { kind: "under_way" },
};

function show(task: Task) {
  const asked: { url: string; body: any }[] = [];
  const answered: number[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    asked.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    return Promise.resolve(new Response(JSON.stringify(task), { status: 202, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <Reviews task={task} rounds={[ROUND]} onAnswered={() => answered.push(1)} />
      </MemoryRouter>
    </MantineProvider>,
  );
  return { asked, answered };
}

test("a held review says it waits, and a finding is dismissed with a reason", async () => {
  const { asked, answered } = show(TASK);
  expect(screen.getByText("Round 1 waits on you before its fix run")).toBeTruthy();
  // A finding already dismissed says who and why, and offers no Dismiss.
  const second = screen.getByTestId("finding-1-2");
  expect(second.textContent).toContain("dismissed by dave: Fixed already.");
  expect(within(second).queryByRole("button")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Dismiss finding 1" }));
  const dialog = await screen.findByRole("dialog", { name: "Dismiss finding 1" });
  expect(dialog.textContent).toContain("Revert the version bump.");
  const confirm = within(dialog).getByRole("button", { name: "Dismiss the finding" }) as HTMLButtonElement;
  expect(confirm.disabled).toBe(true);
  fireEvent.change(within(dialog).getByLabelText(/Why/), { target: { value: "AGENTS.md requires the bump." } });
  fireEvent.click(confirm);

  await waitFor(() => expect(answered).toHaveLength(1));
  expect(asked[0]).toEqual({ url: "/v1/tasks/t-1/reviews/1/findings/1/dismiss", body: { reason: "AGENTS.md requires the bump." } });
});

test("Send to fix now ends the wait", async () => {
  const { asked, answered } = show(TASK);
  fireEvent.click(screen.getByRole("button", { name: "Send to fix now" }));
  await waitFor(() => expect(answered).toHaveLength(1));
  expect(asked[0].url).toBe("/v1/tasks/t-1/reviews/1/proceed");
});

test("a review that is not held offers nothing to dismiss, and still shows what was dismissed", () => {
  show({ ...TASK, waiting_on: null });
  expect(screen.queryByText(/waits on you/)).toBeNull();
  expect(screen.queryByRole("button", { name: /Dismiss finding/ })).toBeNull();
  expect(screen.getByTestId("finding-1-2").textContent).toContain("dismissed by dave");
});
