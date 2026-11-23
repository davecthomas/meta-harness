import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import type { MhEvent } from "../../api";
import { Facts } from "../../components/ui";
import { theme } from "../../theme";
import { provenanceFacts, provenanceOf, provenanceText } from "./Provenance";

let position = 0;
const event = (event_type: string, payload: Record<string, unknown>): MhEvent =>
  ({ position: ++position, event_id: `e-${position}`, event_type, actor: null, run_id: null, task_id: "t-1", payload }) as MhEvent;

const OPENED = event("pull_request_opened", {
  pr_url: "u",
  provenance: { kind: "pull_request", version: "0.49.0", task: "t-1", run: "run-task-t-1:run-1", model: "harness:pi-lean/x:y" },
});
const REVIEWED = (round: string) =>
  event("pull_request_reviewed", { provenance: { kind: "review", version: "0.49.0", task: "t-1", model: "harness:pi-review/a:b", round } });

test("each post's provenance reads as what wrote it, and a post from before provenance shows none", () => {
  const signed = provenanceOf([event("pull_request_opened", { pr_url: "old" }), OPENED, event("pull_request_merged", { provenance: "x" })]);
  expect(signed.map(provenanceText)).toEqual([
    "Pull request: Octofusion 0.49.0, model harness:pi-lean/x:y, run run-task-t-1:run-1",
  ]);
  expect(provenanceText({ what: "Line comment", provenance: { kind: "comment", version: "0.49.0", by: "dave" } })).toBe(
    "Line comment: Octofusion 0.49.0, for dave",
  );
});

test("the task's details show the newest of each kind of post, and nothing when no post carries one", () => {
  expect(provenanceFacts([event("attempt_finished", {})])).toEqual([]);
  render(<MantineProvider theme={theme}><Facts items={provenanceFacts([OPENED, REVIEWED("1"), REVIEWED("2")])} /></MantineProvider>);
  const shown = screen.getByTestId("task-provenance").textContent;
  expect(shown).toContain("Pull request: Octofusion 0.49.0");
  expect(shown).toContain("Review: round 2, Octofusion 0.49.0, model harness:pi-review/a:b");
  expect(shown).not.toContain("round 1");
});
