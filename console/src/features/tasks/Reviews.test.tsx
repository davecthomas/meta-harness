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
  // The verdict and the reviewer read in words, not as the service names them.
  expect(within(first).getByText("Changes asked for")).toBeTruthy();
  expect(within(first).getByTestId("candidate-name").textContent).toBe("claude-opus-5-5, profile pi-review");
  expect(within(first).getByTestId("candidate-name").getAttribute("title")).toBe("harness:pi-review/claude:claude-opus-5-5");
  expect(within(screen.getByTestId("review-2")).getByText("Clean")).toBeTruthy();
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


test("a round named in the address is marked, as a review's box on the timeline opens it", () => {
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/tasks/t-1?tab=reviews&round=2"]}>
        <Reviews task={TASK} rounds={ROUNDS} />
      </MemoryRouter>
    </MantineProvider>,
  );
  expect(screen.getByTestId("review-2").getAttribute("data-asked")).toBe("true");
  expect(screen.getByTestId("review-1").getAttribute("data-asked")).toBeNull();
});

test("each finding says what became of it: fixed or not by the next reviewer, and nothing when no one judged it", () => {
  const rounds: ReviewRound[] = [
    { ...ROUNDS[0], findings: [
      { n: 1, id: "1.1", path: "a.py", line: 3, body: "Test it.", outcome: "fixed" },
      { n: 2, id: "1.2", path: null, line: null, body: "Name it.", outcome: "not_fixed" },
      { n: 3, id: "1.3", path: null, line: null, body: "Doc it.", outcome: null },
    ] },
  ];
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <Reviews task={TASK} rounds={rounds} />
      </MemoryRouter>
    </MantineProvider>,
  );
  expect(within(screen.getByTestId("finding-1-1")).getByText("Fixed")).toBeTruthy();
  expect(within(screen.getByTestId("finding-1-2")).getByText("Not fixed")).toBeTruthy();
  expect(within(screen.getByTestId("finding-1-3")).queryByText(/fixed/i)).toBeNull();
});
