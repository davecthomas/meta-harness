import { act, cleanup, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import type { MhEvent, PullCheck, PullChecks } from "../../api";
import { describe as terminalLine } from "../../terminal";
import { theme } from "../../theme";
import { Checks, checksLine, useChecks } from "./Checks";
import { NowLine } from "./TaskPath";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const check = (name: string, more: Partial<PullCheck> = {}): PullCheck => ({
  name, status: "completed", conclusion: "success", started_at: "2026-11-18T23:35:00Z", completed_at: "2026-11-18T23:36:40Z", seconds: 100, usual_seconds: 100, url: `https://github.com/o/r/runs/${name}`, ...more,
});
const going = (name: string, usual: number) => check(name, { status: "in_progress", conclusion: null, completed_at: null, seconds: null, usual_seconds: usual });
const CHECKS: PullChecks = {
  repo: "o/r", number: 9, sha: "d778a1925124", of: "pull_request", state: "pending", passed: 2, failed: [], total: 4, usual_seconds: 240,
  checks: [going("sandbox", 240), check("tests"), check("scan", { conclusion: "skipped", seconds: 1 }), going("e2e", 200)],
};
const NOW = Date.parse("2026-11-18T23:37:10Z");

test("the checks read in one line: how many passed, the slowest still going, and how long it usually takes", () => {
  expect(checksLine(CHECKS, NOW)).toBe("2 of 4 passed, sandbox running 2m 10s and 1 more, usually about 4m in all");
  const failed = { ...CHECKS, state: "failed" as const, passed: 3, failed: ["tests"], checks: CHECKS.checks.map((c) => ({ ...c, status: "completed", seconds: 100 })) };
  expect(checksLine(failed, NOW)).toBe("3 of 4 passed, failed: tests");
  expect(checksLine({ ...CHECKS, total: 0, checks: [] }, NOW)).toBe("No check has started yet");
  expect(checksLine(null, NOW)).toBe("");
  // A check of the default branch that does not run here, such as a deploy, does not lengthen "usually".
  expect(checksLine({ ...CHECKS, usual_seconds: 462 }, NOW)).toContain("usually about 4m in all");
});

test("the Checks tab lists each check with its state, its time counted by the second, its usual time and its log", () => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  render(<MantineProvider theme={theme}><Checks checks={CHECKS} error={null} /></MantineProvider>);

  expect(screen.getByTestId("checks-summary").textContent).toContain("2 of 4 passed, sandbox running 2m 10s");
  expect(screen.getByTestId("checks-summary").textContent).toContain("pull request #9's newest commit, d778a19");
  const sandbox = screen.getByTestId("check-sandbox").textContent ?? "";
  expect(sandbox).toContain("Running");
  expect(sandbox).toContain("2m 10s so far");
  expect(sandbox).toContain("4m");
  expect(screen.getByTestId("check-tests").textContent).toContain("Passed1m 40s");
  expect(screen.getByTestId("check-scan").textContent).toContain("Skipped");
  expect(screen.getAllByRole("link", { name: "Log" })[0].getAttribute("href")).toBe("https://github.com/o/r/runs/sandbox");

  act(() => void vi.advanceTimersByTime(5000));
  expect(screen.getByTestId("check-sandbox").textContent).toContain("2m 15s so far");
});

test("checks that cannot be read say why, and a later failure keeps the last ones on show", () => {
  const { rerender } = render(<MantineProvider theme={theme}><Checks checks={null} error="GitHub did not answer" /></MantineProvider>);
  expect(screen.getByTestId("read-failed").textContent).toBe("Could not read this task's checks: GitHub did not answer. It is read again every few seconds.");
  rerender(<MantineProvider theme={theme}><Checks checks={CHECKS} error="GitHub did not answer" /></MantineProvider>);
  expect(screen.getByText(/The last read failed, so this may be out of date/)).toBeTruthy();
  expect(screen.getByTestId("check-tests")).toBeTruthy();
});

test("checks are read every few seconds while any is going, and not at all when nobody is looking", async () => {
  vi.useFakeTimers();
  const asked: string[] = [];
  vi.stubGlobal("fetch", async (url: string) => {
    asked.push(url);
    return new Response(JSON.stringify(CHECKS), { status: 200 });
  });
  function Watch({ active }: { active: boolean }) {
    const { checks } = useChecks("t-1", active);
    return <p>{checks ? checks.state : "none"}</p>;
  }
  const { rerender } = render(<Watch active={false} />);
  await act(() => vi.advanceTimersByTimeAsync(11_000));
  expect(asked).toEqual([]);

  rerender(<Watch active />);
  await act(() => vi.advanceTimersByTimeAsync(11_000));
  expect(asked).toEqual(["/v1/pulls/t-1/checks", "/v1/pulls/t-1/checks", "/v1/pulls/t-1/checks"]);
  expect(screen.getByText("pending")).toBeTruthy();
  // A page in the background is read once, then left alone until it is looked at.
  cleanup();
  asked.length = 0;
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  render(<Watch active />);
  await act(() => vi.advanceTimersByTimeAsync(11_000));
  expect(asked).toHaveLength(1);
  hidden.mockReturnValue(false);
  await act(async () => void document.dispatchEvent(new Event("visibilitychange")));
  expect(asked).toHaveLength(2);
  hidden.mockRestore();
});

let n = 0;
const ev = (event_type: string, at: string, payload: Record<string, unknown> = {}, run_id: string | null = null): MhEvent => ({
  position: ++n, event_id: String(n), event_type, time_utc: at, source: "", actor: null, run_id, task_id: "t", payload,
});

test("the line above the terminal, and a board's card, say the task waits for checks and how they stand", () => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  const events = [
    ev("task_created", "2026-11-18T23:30:00Z", { status: "queued" }),
    ev("run_prompt", "2026-11-18T23:31:00Z", { kind: "task" }, "t:run-1"),
    ev("attempt_finished", "2026-11-18T23:34:00Z", { attempt: 1, tier: "standard", candidate: "harness:a/b:c", decision: "finished", spent_usd: "0.1" }, "t:run-1"),
    ev("pull_request_opened", "2026-11-18T23:35:00Z", { pr_url: "https://github.com/o/r/pull/9", files: ["a"] }),
  ];
  const { rerender } = render(<MantineProvider theme={theme}><NowLine events={events} checks={CHECKS} /></MantineProvider>);
  expect(screen.getByTestId("now-line").textContent).toBe("Now: Waiting for the pull request's checks, 2m 10s so far. 2 of 4 passed, sandbox running 2m 10s and 1 more, usually about 4m in all");

  rerender(<MantineProvider theme={theme}><NowLine events={events} compact /></MantineProvider>);
  expect(screen.getByTestId("now-line").textContent).toBe("Waiting for the pull request's checks, 2m 10s");

  // Once the checks are in, nothing is going until the review's run records its start.
  rerender(<MantineProvider theme={theme}><NowLine events={[...events, ev("pull_request_checks", "2026-11-18T23:39:00Z", { state: "passed", failed: [] })]} compact /></MantineProvider>);
  expect(screen.queryByTestId("now-line")).toBeNull();
});

test("the terminal says when a wait for checks starts and how it ends", () => {
  const text = (type: string, payload: Record<string, unknown>) => terminalLine(ev(type, "2026-11-18T23:35:00Z", payload))?.text.slice(9);
  expect(text("pull_request_opened", { pr_url: "https://github.com/o/r/pull/9" })).toBe("pull request https://github.com/o/r/pull/9 opened. Waiting for the code host's checks: several minutes with nothing to print");
  expect(text("attempt_finished", { attempt: 3, tier: "fix", candidate: "harness:a/b:c", decision: "finished", spent_usd: "0.1" })).toContain("the code host's checks run on them");
  expect(text("attempt_finished", { attempt: 1, tier: "standard", candidate: "harness:a/b:c", decision: "finished", spent_usd: "0.1" })).toBe("attempt 1 on harness:a/b:c: finished, $0.10");
  expect(text("pull_request_checks", { state: "passed", failed: [], head_sha: "d778a1925124" })).toBe("checks passed at d778a19");
  expect(text("pull_request_checks", { state: "failed", failed: ["tests"] })).toBe("checks failed: tests");
  expect(text("pull_request_reviewed", { round: 1, verdict: "changes", findings: 2 })).toBe("review round 1: 2 findings, changes asked");
  expect(text("pull_request_merged", { merge_sha: "58e544902fca" })).toBe("merged as 58e5449. Waiting for the merge commit's checks");
  expect(text("merge_checks", { state: "passed", failed: [] })).toBe("merge commit's checks passed");
});
