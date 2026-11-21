import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { expect, test } from "vitest";
import type { MhEvent, Task } from "../../api";
import { theme } from "../../theme";
import { diagnose } from "./diagnosis";
import { nextSteps } from "./nextSteps";
import { TaskFailure } from "./TaskFailure";

const PR = "https://github.com/o/r/pull/437";

function reviewed(verdict: string, position: number): MhEvent {
  return { position, event_type: "pull_request_reviewed", payload: { round: position, verdict } } as unknown as MhEvent;
}

function show(reason: string, events: MhEvent[]) {
  const task = { id: "t-1", status: "review_unresolved", status_reason: reason, pr_url: PR, complexity: "high", max_usd: "20.00", external_ref: "jira:ABC-1" } as unknown as Task;
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <TaskFailure task={task} status="review_unresolved" events={events} />
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("the workflow's reason for a clean review a conflict kept from merging recommends Merge", () => {
  const found = diagnose({
    status: "review_unresolved",
    reason: "review round 11 by r came back clean at 06daae279d3d, but the pull request conflicts with its base. No fix round was left to resolve it: the loop used its 3 fix rounds",
  });
  expect(found?.kind).toBe("clean_conflict");
  expect(nextSteps(found!, { status: "review_unresolved", pr_url: PR, complexity: "high" })).toEqual({ stronger: false, merge: true, recommended: "merge" });
});

test("an older task whose last review was clean says so, and its first button is the recommended Merge", () => {
  show("the review loop used its 3 fix rounds", [reviewed("changes", 1), reviewed("clean", 2)]);

  expect(screen.getByText("The review is clean, and the merge waits on a conflict")).toBeTruthy();
  const buttons = screen.getAllByRole("button");
  expect(buttons[0].textContent).toBe("Merge (recommended)");
  expect(screen.queryByRole("button", { name: /stronger model/ })).toBeNull();
});

test("a last review that asked for changes keeps its findings message and puts the recommended step first", () => {
  show("the review loop used its 3 fix rounds", [reviewed("clean", 1), reviewed("changes", 2)]);

  expect(screen.getByText("The review loop ended without a clean review")).toBeTruthy();
  // Already on the stronger tier: a person reads the review, so that is the first button.
  expect(screen.getAllByRole("link")[0].textContent).toBe("Read the last review (recommended)");
});
