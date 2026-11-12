import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { RemoveIssue, removeEffect } from "./RemoveIssue";

afterEach(() => vi.unstubAllGlobals());

test("removing an issue says what it does to the issue's task", () => {
  expect(removeEffect(undefined).text).toContain("no task yet");
  expect(removeEffect({ status: "queued", pr_url: null }).text).toBe("Its task has not started: it is canceled.");
  expect(removeEffect({ status: "running", pr_url: null }).text).toContain("the run is stopped");
  expect(removeEffect({ status: "ready", pr_url: "https://github.com/o/r/pull/9" })).toEqual({
    text: "Its task is canceled. Its pull request is open with work done: it stays open on GitHub, for you to merge or close there.",
    leavesPullRequest: true,
  });
  expect(removeEffect({ status: "closed", pr_url: "x" }).leavesPullRequest).toBe(false);
});

test("Remove from goal asks first, then removes the issue, forcing it only when a pull request is left open", async () => {
  const asked: [string, string | undefined][] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    asked.push([url, init?.method]);
    return Promise.resolve(new Response(JSON.stringify({ dropped: "github:o/r#7", task_canceled: "t-1" }), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  const onRemoved = vi.fn();
  render(
    <MantineProvider theme={theme}>
      <RemoveIssue goalId="g-1" issueRef="github:o/r#7" task={{ status: "ready", pr_url: "https://github.com/o/r/pull/9" }} onRemoved={onRemoved} />
    </MantineProvider>,
  );

  fireEvent.click(screen.getByRole("button", { name: "Remove o/r#7 from the goal" }));
  expect((await screen.findByTestId("remove-issue")).textContent).toContain("it stays open on GitHub");
  expect(asked).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Remove from goal" }));

  await waitFor(() => expect(onRemoved).toHaveBeenCalled());
  expect(asked).toEqual([["/v1/goals/g-1/issues/github:o/r%237?force=true", "DELETE"]]);
});
