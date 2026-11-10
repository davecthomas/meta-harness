import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import type { ReviewRound, Task } from "../../api";
import { theme } from "../../theme";
import { Reviews } from "./Reviews";

const TASK = {
  id: "t-1", external_ref: "github:o/r#1", repo: "o/r", task_type: "bug", title: "Fix", status: "ready", status_reason: "clean, auto-merge off",
  cancel_requested: false, goal_id: null, pr_url: "https://github.com/o/r/pull/9", acceptance_checks: [], created_at: "", updated_at: "",
} as Task;

const ROUNDS: ReviewRound[] = [
  { round: 1, verdict: "changes", reviewer: "harness:pi-review/claude:claude-opus-5-5", head_sha: "abc1234ff", summary: "Two gaps.",
    findings: [{ path: "a.py", line: 3, body: "Test it." }], review_url: "https://github.com/o/r/pull/9#r1", reviewed_at: null, run_id: "r-2",
    next: { kind: "fix", run_id: "r-3", attempt: 3 } },
  { round: 2, verdict: "clean", reviewer: "harness:pi-review/claude:claude-opus-5-5", head_sha: "def5678ff", summary: "Good.",
    findings: [], review_url: null, reviewed_at: null, run_id: "r-4", next: { kind: "waiting", reason: "clean, auto-merge off" } },
];

test("each round shows its verdict, findings linked at the reviewed commit, and what came next", () => {
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <Reviews task={TASK} rounds={ROUNDS} />
      </MemoryRouter>
    </MantineProvider>,
  );
  const first = screen.getByTestId("review-1");
  expect(within(first).getByText("changes")).toBeTruthy();
  expect(within(first).getByText("a.py:3").getAttribute("href")).toBe("https://github.com/o/r/blob/abc1234ff/a.py#L3");
  expect(within(first).getByText(/fix run 3/)).toBeTruthy();
  expect(within(screen.getByTestId("review-2")).getByText(/Waiting for an operator/)).toBeTruthy();
});

test("a pull request with no review yet says why", () => {
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <Reviews task={{ ...TASK, status: "running" }} rounds={[]} />
      </MemoryRouter>
    </MantineProvider>,
  );
  expect(screen.getByText(/the checks or the review run are under way/)).toBeTruthy();
});
