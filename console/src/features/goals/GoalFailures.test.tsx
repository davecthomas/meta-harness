import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { expect, test } from "vitest";
import type { IssueProgress, Task } from "../../api";
import { theme } from "../../theme";
import { GoalFailures } from "./GoalFailures";

function issue(ref: string, fields: Partial<IssueProgress>): IssueProgress {
  return { ref, repo: "o/r", state: "open", depends_on: [], refused_reason: null, task_id: null, task_status: null, task_status_reason: null, pr_url: null, pr_state: null, checks: null, ...fields };
}

function show(issues: IssueProgress[]) {
  return render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <GoalFailures issues={issues} tasks={{ "t-1": { id: "t-1", title: "Record prompts" } as Task }} />
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("a goal lists each failed or unresolved task with why, linked to it, and each refused issue", () => {
  show([
    issue("github:o/r#1", { task_id: "t-1", task_status: "failed", task_status_reason: "deploy failed on the merge commit" }),
    issue("github:o/r#2", { task_id: "t-2", task_status: "review_unresolved", task_status_reason: "used its 3 fix rounds" }),
    issue("github:o/r#3", { refused_reason: "it depends on itself" }),
    issue("github:o/r#4", { task_id: "t-4", task_status: "running" }),
  ]);
  const alert = screen.getByRole("alert");
  expect(alert.textContent).toContain("3 issues need you");
  expect(alert.textContent).toContain("failed: deploy failed on the merge commit");
  expect(alert.textContent).toContain("o/r#2 (review_unresolved: used its 3 fix rounds)");
  expect(alert.textContent).toContain("o/r#3 (refused: it depends on itself)");
  expect(alert.textContent).not.toContain("o/r#4");
  expect(screen.getByRole("link", { name: "Record prompts" }).getAttribute("href")).toBe("/tasks/t-1");
});

test("a goal with nothing failed shows no alert", () => {
  show([issue("github:o/r#4", { task_id: "t-4", task_status: "running" })]);
  expect(screen.queryByRole("alert")).toBeNull();
});
