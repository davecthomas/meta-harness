import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { IssueProgress, Task } from "../../api";
import { theme } from "../../theme";
import { GoalFailures, groups } from "./GoalFailures";

afterEach(() => vi.unstubAllGlobals());

const GRANT =
  "TaskFailed: run 1 on harness:pi-careful/claude:claude-opus-5-5 ended for good: run_harness failed (HarnessConfigError): HarnessConfigError: the HTTP Gateway refused the run grant (422): [{'type': 'string_pattern_mismatch', 'loc': ['body', 'spend_limit_usd'], 'msg': \"String should match pattern '^[0-9]{1,6}'\", 'input': '1000000.00'}]";

function issue(ref: string, fields: Partial<IssueProgress>): IssueProgress {
  return { ref, repo: "o/r", state: "open", depends_on: [], refused_reason: null, task_id: null, task_status: null, task_status_reason: null, pr_url: null, pr_state: null, checks: null, ...fields };
}

function show(issues: IssueProgress[], onChanged?: () => void) {
  return render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <GoalFailures issues={issues} tasks={{ "t-1": { id: "t-1", title: "Record prompts", max_usd: "12" } as Task }} onChanged={onChanged} />
      </MemoryRouter>
    </MantineProvider>,
  );
}

const ISSUES = [
  issue("github:o/r#1", { task_id: "t-1", task_status: "failed", task_status_reason: GRANT }),
  issue("github:o/r#5", { task_id: "t-5", task_status: "failed", task_status_reason: GRANT.replace("pi-careful", "pi-lean") }),
  issue("github:o/r#2", { task_id: "t-2", task_status: "review_unresolved", task_status_reason: "the review loop used its 3 fix rounds" }),
  issue("github:o/r#3", { refused_reason: "the issue has no ## Acceptance checks and the repo registry names no default checks" }),
  issue("github:o/r#4", { task_id: "t-4", task_status: "running" }),
];

test("issues that failed the same way are one message: what happened, why, and what to do", () => {
  expect(groups(ISSUES).map((g) => [g.diagnosis.kind, g.items.length])).toEqual([
    ["grant_spend_limit", 2],
    ["fix_rounds", 1],
    ["refused_no_checks", 1],
  ]);
  show(ISSUES);
  const alert = screen.getByRole("alert");
  expect(alert.textContent).toContain("4 issues need you");
  const grant = within(screen.getByTestId("failure-grant_spend_limit"));
  expect(grant.getByText("The run could not start: the HTTP Gateway refused its spend limit (2 tasks)")).toBeTruthy();
  expect(grant.getByText(/asked for a \$1,000,000.00 spend limit/)).toBeTruthy();
  expect(grant.getByText(/Retry the task\./)).toBeTruthy();
  expect(grant.getByRole("link", { name: "Record prompts" }).getAttribute("href")).toBe("/tasks/t-1");
  expect(grant.getByRole("button", { name: "Retry all 2" })).toBeTruthy();
  // The recorded error is there when asked for, without the wrappers around it.
  expect(alert.textContent).not.toContain("TaskFailed: run 1");
  fireEvent.click(grant.getByRole("button", { name: "Show the recorded error" }));
  expect(grant.getByText(/^the HTTP Gateway refused the run grant \(422\)/)).toBeTruthy();

  expect(within(screen.getByTestId("failure-fix_rounds")).getByText("The review still asked for changes after 3 fix rounds")).toBeTruthy();
  const refused = within(screen.getByTestId("failure-refused_no_checks"));
  expect(refused.getByText(/Add an "## Acceptance checks" section/)).toBeTruthy();
  expect(refused.getByRole("link", { name: "Edit the issue on GitHub" }).getAttribute("href")).toBe("https://github.com/o/r/issues/3");
  expect(refused.queryByRole("button", { name: /Retry/ })).toBeNull();
  expect(alert.textContent).not.toContain("o/r#4");
});

test("Retry all sends each task of the group back to the queue with its own spend limit", async () => {
  const posted: [string, unknown][] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    posted.push([url, JSON.parse(String(init?.body))]);
    return Promise.resolve(new Response(JSON.stringify({ id: "t" }), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  const onChanged = vi.fn();
  show(ISSUES.slice(0, 2), onChanged);

  fireEvent.click(screen.getByRole("button", { name: "Retry all 2" }));

  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(posted).toEqual([
    ["/v1/tasks/t-1/retry", { max_usd: 12 }],
    ["/v1/tasks/t-5/retry", { max_usd: 10 }],
  ]);
  expect(await screen.findByRole("button", { name: "Sent back to the queue" })).toBeTruthy();
});

test("a goal with nothing failed shows no alert", () => {
  show([issue("github:o/r#4", { task_id: "t-4", task_status: "running" })]);
  expect(screen.queryByRole("alert")).toBeNull();
});
