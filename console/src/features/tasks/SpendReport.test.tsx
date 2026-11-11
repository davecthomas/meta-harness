import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { TaskSpend } from "../../api";
import { theme } from "../../theme";
import { cachedShare, SpendReport, tokens } from "./SpendReport";

afterEach(() => vi.unstubAllGlobals());

const run = (attempt: number, kind: string, usd: string, more: Partial<TaskSpend["runs"][number]> = {}) => ({
  run_id: `t:run-${attempt}`, attempt, kind, tier: kind === "task" ? "complex" : kind, candidate: "harness:pi-careful/claude:claude-opus-5-5",
  turns: 13, model_calls: 14, input_tokens: 550_224, cached_input_tokens: 500_000, output_tokens: 2_000, usd, status: "FINISHED", end_reason: "completed", ...more,
});
const SPEND: TaskSpend = {
  task_id: "t", max_usd: "10.00",
  runs: [
    run(1, "task", "4.0044", { turns: 70, input_tokens: 8_137_130, cached_input_tokens: 7_000_000 }),
    run(2, "review", "0.2425"),
    run(3, "fix", "0.8500", { status: "ERROR", end_reason: "spend_limit", turns: null, input_tokens: null, cached_input_tokens: null, output_tokens: null }),
  ],
  totals: { usd: "5.0969", by_kind: { task: "4.0044", review: "0.2425", fix: "0.8500" }, runs: 3, turns: 83, input_tokens: 8_687_354, cached_input_tokens: 7_500_000, output_tokens: 4_000 },
};

function show(spend: TaskSpend) {
  vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify(spend), { status: 200, headers: { "Content-Type": "application/json" } })));
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <SpendReport taskId="t" status="failed" />
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("the spend tab shows each run's kind, turns, tokens and cost, marks the dearest, and totals against the limit", async () => {
  show(SPEND);
  const first = await screen.findByTestId("spend-run-1");
  expect(first.textContent).toContain("task");
  expect(first.textContent).toContain("70");
  expect(first.textContent).toContain("8.1M");
  expect(first.textContent).toContain("86%");
  expect(first.textContent).toContain("$4.00");
  expect(first.textContent).toContain("dearest");
  expect(screen.getByTestId("spend-run-2").textContent).not.toContain("dearest");
  expect(screen.getByTestId("spend-run-3").textContent).toContain("ERROR (spend_limit)");
  expect(screen.getByRole("link", { name: "Run 3" }).getAttribute("href")).toBe("/tasks/t?run=t%3Arun-3");
  const totals = screen.getByTestId("spend-totals").textContent ?? "";
  expect(totals).toContain("$5.10 spent of $10.00, in 3 runs and 83 turns");
  expect(totals).toContain("review: $0.24");
  expect(totals).toContain("fix: $0.85");
  expect(totals).toContain("8.7M tokens in (86% from the cache)");
});

test("a task with no finished run says nothing is spent", async () => {
  show({ ...SPEND, runs: [], totals: { ...SPEND.totals, usd: "0", runs: 0 } });
  expect(await screen.findByText("No run has finished yet, so nothing is spent.")).toBeTruthy();
});

test("token counts are shortened, and the cached share is a whole percent", () => {
  expect([tokens(8_137_130), tokens(550_224), tokens(900), tokens(null)]).toEqual(["8.1M", "550k", "900", ""]);
  expect([cachedShare(1000, 250), cachedShare(0, 0), cachedShare(1000, null)]).toEqual(["25%", "", ""]);
});
