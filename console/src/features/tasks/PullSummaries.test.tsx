import { render, screen, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import type { MhEvent } from "../../api";
import { theme } from "../../theme";
import { entries } from "./Timeline";
import { steps } from "./path";
import { summariesOf, summaryLine } from "./summaries";
import { OpeningSummaryBadge, PullSummaries } from "./PullSummaries";

let n = 0;
const ev = (event_type: string, payload: Record<string, unknown>, run_id: string | null = null): MhEvent => ({
  position: ++n, event_id: String(n), event_type, time_utc: new Date(Date.parse("2026-11-20T10:00:00Z") + n * 60_000).toISOString(),
  source: "run", actor: null, run_id, task_id: "t", payload,
});
const summary = (fix_round: number, source: string, text: string, heading = "Summary from the run") =>
  ev("pull_request_summary", { pr_url: "https://github.com/o/r/pull/9", fix_round, source, heading, text }, `t:run-${fix_round + 1}`);

const show = (node: React.ReactNode) => render(<MantineProvider theme={theme}>{node}</MantineProvider>);

test("the recorded summaries are read in round order, each round once, and an unknown source is skipped", () => {
  const read = summariesOf([
    summary(1, "files", "first try"),
    summary(0, "run", "Fixed the save path."),
    summary(1, "files", "The run ended without a usable summary"),
    summary(2, "guessed", "?"),
    ev("pull_request_opened", { pr_url: "https://github.com/o/r/pull/9" }),
  ]);
  expect(read.map((s) => [s.round, s.source, s.text])).toEqual([
    [0, "run", "Fixed the save path."],
    [1, "files", "The run ended without a usable summary"],
  ]);
});

test("each summary's line says whose words it is", () => {
  expect(summaryLine({ fix_round: 0, source: "run" })).toBe("The pull request's summary: the run's own words");
  expect(summaryLine({ fix_round: 0, source: "recorded" })).toBe(
    "The pull request's summary: written by Muxxy from its record of the run, as the run left none it could use",
  );
  expect(summaryLine({ fix_round: 2, source: "files" })).toBe(
    "Fix round 2's summary: written by Muxxy from the files the branch changes, as the run left none it could use",
  );
});

test("the timeline and the path both say where the summary came from", () => {
  // Recorded in Publish, after the run it summarizes.
  const created = ev("task_created", { status: "queued" });
  const prompt = ev("run_prompt", { kind: "task", template: "repo-task-v1", prompt: "Do it" }, "t:run-1");
  const recorded = summary(0, "recorded", "The run ended without a usable summary, so Muxxy wrote this one");
  expect(entries([recorded])).toEqual([{ kind: "status", position: recorded.position, text: summaryLine(recorded.payload), error: false }]);
  const path = steps([created, prompt, recorded]);
  expect(path.flatMap((s) => s.notes)).toContain(summaryLine(recorded.payload));
});

test("the pull request view shows each summary with its source, and says when Muxxy wrote one", () => {
  show(
    <PullSummaries
      events={[
        summary(0, "run", "Fixed the save path; uv run pytest -q passes."),
        summary(1, "recorded", "The run ended without a usable summary, so Muxxy wrote this one.", "Summary written by Muxxy"),
      ]}
    />,
  );
  const opening = screen.getByTestId("pull-summary-0");
  expect(within(opening).getByText("Summary from the run")).toBeTruthy();
  expect(within(opening).getByTestId("summary-source").textContent).toBe("The run's own words");
  expect(within(opening).getByText("Fixed the save path; uv run pytest -q passes.")).toBeTruthy();
  const fix = screen.getByTestId("pull-summary-1");
  expect(within(fix).getByText("Fix round 1")).toBeTruthy();
  expect(within(fix).getByTestId("summary-source").textContent).toBe("Written by Muxxy");
  expect(fix.textContent).toContain("from its record of the run");
});

test("a pull request with no recorded summary shows no summary and no badge", () => {
  const { container } = show(
    <>
      <PullSummaries events={[ev("pull_request_opened", { pr_url: "https://github.com/o/r/pull/9" })]} />
      <OpeningSummaryBadge events={[]} />
    </>,
  );
  expect(screen.queryByTestId("pull-summaries")).toBeNull();
  expect(container.querySelector("[data-testid='summary-source']")).toBeNull();
});

test("the task's facts badge the opening summary's source", () => {
  show(<OpeningSummaryBadge events={[summary(0, "files", "The run ended without a usable summary"), summary(1, "run", "Fixed the review's findings.")]} />);
  expect(screen.getByTestId("summary-source").textContent).toBe("Written by Muxxy");
});
