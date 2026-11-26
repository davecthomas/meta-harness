import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { Costs } from "../../api";
import { theme } from "../../theme";
import { axisTicks, bucketLabel, seriesColor } from "./CostChart";
import { composition } from "./GoalCosts";
import { PerformancePage, costLabel, rangeDays } from "./PerformancePage";

afterEach(() => vi.unstubAllGlobals());

const COSTS: Costs = {
  from: "2026-11-09", to: "2026-11-11", bucket: "day", group_by: "source",
  buckets: ["2026-11-09", "2026-11-10", "2026-11-11"],
  series: [{ key: "model", usd: [0, 3.25, 1.26], total: 4.51 }, { key: "cloud", usd: [0, 4.2, 0], total: 4.2 }],
  totals: [0, 7.45, 1.26],
  trend: { slope: 0.63, start: 2.27, end: 3.53 },
  total: 8.71, daily_average: 2.9033, previous_total: 4, change_percent: 117.8, projected_month: 105.9,
  top_tasks: [{ task_id: "t-1", title: "Fix it", usd: 3.25 }],
  labels: {}, choices: { source: ["cloud", "model"], kind: ["planning", "review", "task"], goal: ["g-a"], repo: ["o/r"], model: ["claude-opus-5-5", "claude-sonnet-5"] },
  sources: {
    model: { configured: true, note: "What model calls cost, from each run's grant." },
    cloud: { configured: false, note: "Not connected: turn on the billing export to BigQuery and set MH_BILLING_EXPORT_TABLE." },
    temporal: { configured: false, note: "Temporal runs on the VM, so its cost is inside Compute Engine's, under cloud." },
  },
  scope: "all",
};

const GOAL_COSTS = {
  from: "",
  to: "",
  kinds: ["planning", "task", "review", "fix", "writing", "narratives"],
  goals: [
    { goal_id: "g-a", title: "Saves", status: "met", total: 10, parts: { planning: 1, task: 6, review: 2, fix: 1 } },
    // Still running: not part of the average while a completed goal is.
    { goal_id: "g-b", title: "Exports", status: "active", total: 40, parts: { task: 40 } },
  ],
};

const NUMBERS = {
  since: "",
  until: "",
  tasks: 12,
  merged: 9,
  cost_per_task: { average: 5.6667, median: 4.5, count: 12 },
  cost_per_pull_request: { average: 6.25, median: 5, count: 10 },
  cost_per_goal: { average: 34, median: 30, count: 2 },
  dollar_hours_per_completed_task: { average: 2.345, median: 1.5, count: 9 },
  tries_to_green: { average: 1.4, median: 1, count: 9 },
  first_try_green: { rate: 0.6667, count: 6, of: 9 },
  total_usd: "68.00",
  wasted_usd: "11.00",
  review_loop_usd: "20.00",
  hours: 14.5,
};

// asked: the cost reads only, as the tests below count them; the key numbers answer beside them.
function show(costs: Costs, path = "/performance", numbers: unknown = NUMBERS, all: string[] = []) {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    all.push(url);
    if (url.startsWith("/v1/performance")) return Promise.resolve(new Response(JSON.stringify(numbers), { status: 200, headers: { "Content-Type": "application/json" } }));
    if (url.startsWith("/v1/costs/goals")) return Promise.resolve(new Response(JSON.stringify(GOAL_COSTS), { status: 200, headers: { "Content-Type": "application/json" } }));
    // The similar goals table reads its own list (SimilarShown.test.tsx); these tests are about costs.
    if (url.startsWith("/v1/goals/similar")) return Promise.resolve(new Response(JSON.stringify({ shown: [], cutoff: 0.7, model: "m" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    asked.push(url);
    return Promise.resolve(new Response(JSON.stringify(costs), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(<MantineProvider theme={theme}><MemoryRouter initialEntries={[path]}><PerformancePage /></MemoryRouter></MantineProvider>);
  return asked;
}

test("the axis steps are round, a range ends today, and a series keeps its color whatever is filtered", () => {
  expect(axisTicks(7.45)).toEqual([0, 2, 4, 6, 8]);
  expect(axisTicks(0.4)).toEqual([0, 0.1, 0.2, 0.3, 0.4]);
  expect(axisTicks(0)).toEqual([0, 1]);
  expect(rangeDays(7, new Date("2026-11-11T15:00:00Z"))).toEqual({ from: "2026-11-05", to: "2026-11-11" });
  expect(bucketLabel("2026-11-09", "day")).toBe("Nov 9");
  const order = ["cloud", "model"];
  expect(seriesColor("model", order)).toBe("var(--series-2)");
  expect(seriesColor("model", order)).toBe(seriesColor("model", order.slice()));
  expect(seriesColor("other", order)).toContain("gray");
  expect(costLabel("cloud", COSTS)).toBe("Cloud");
  expect(costLabel("Compute Engine", { labels: {}, group_by: "kind" }, "cloud")).toBe("Compute Engine");
  expect(costLabel("task", { labels: {}, group_by: "model" })).toBe("task");
});

test("the page says what the work costs, under the old name nowhere", async () => {
  show(COSTS);
  expect(await screen.findByRole("heading", { name: "Performance" })).toBeTruthy();
  expect(document.body.textContent).not.toMatch(/meta-harness/i);
});

test("an admin sees the totals, the chart with its trend, where the money went and each source", async () => {
  const asked = show(COSTS);
  const stats = await screen.findByTestId("cost-stats");
  expect(stats.textContent).toContain("$8.71");
  expect(stats.textContent).toContain("$2.90");
  expect(stats.textContent).toContain("Change from the 3 days before");
  expect(stats.textContent).toContain("+117.8%");
  expect(stats.textContent).toContain("$4.00 in the 3 days before");
  expect(stats.textContent).toContain("$105.90");
  expect(stats.textContent).toContain("Rising $0.63 a day");
  expect(asked[0]).toMatch(/^\/v1\/costs\?from=\d{4}-\d\d-\d\d&to=\d{4}-\d\d-\d\d&bucket=day&group_by=source$/);

  const chart = screen.getByTestId("cost-chart");
  expect(chart.querySelectorAll("path[data-series]").length).toBe(3);
  expect(screen.getByTestId("trend-line")).toBeTruthy();
  expect(screen.getByTestId("cost-legend").textContent).toBe("AICloudTrend");
  const breakdown = screen.getByTestId("cost-breakdown").textContent ?? "";
  expect(breakdown).toContain("AI$4.51 52%");
  expect(breakdown).toContain("Cloud$4.20 48%");
  expect(screen.getByTestId("cost-breakdown-total").textContent).toBe("Total$8.71");
  expect(screen.getByTestId("top-tasks-total").textContent).toBe("Total of these 1$3.25");
  expect(screen.getByRole("link", { name: "Fix it" }).getAttribute("href")).toBe("/tasks/t-1?tab=spend");
  expect(screen.getByTestId("cost-sources").textContent).toContain("Not connected: turn on the billing export");
  expect(screen.queryByTestId("own-scope")).toBeNull();

  // Hovering a bucket lists every series in it.
  fireEvent.pointerEnter(chart.querySelectorAll("rect")[1]);
  const tip = await screen.findByRole("tooltip");
  expect(tip.textContent).toContain("Nov 10");
  expect(tip.textContent).toContain("$7.45Total");
  expect(tip.textContent).toContain("$4.20Cloud");
});

test("the cost category sets the source, and each category has its own breakdowns and filters", async () => {
  const asked = show(COSTS, "/performance?category=ai&by=model&model=claude-opus-5-5&goal=g-a");
  await screen.findByTestId("cost-stats");
  expect(asked[0]).toContain("group_by=model");
  expect(asked[0]).toContain("model=claude-opus-5-5");
  expect(asked[0]).toContain("goal=g-a");
  expect(asked[0]).toContain("source=model");
  const breakdowns = [...(screen.getByLabelText("Break down by") as HTMLSelectElement).options].map((o) => o.textContent);
  expect(breakdowns).toEqual(["Model", "Run type", "Goal", "Repository", "Operator", "Label", "Pull request outcome"]);
  expect(screen.queryByText("Kind of work")).toBeNull();
  // A filter with a value stays, so it can be cleared, though it has one choice.
  expect(screen.getAllByText("Goal").some((e) => e.tagName === "LABEL")).toBe(true);

  fireEvent.click(screen.getByRole("radio", { name: "Cloud" }));
  await waitFor(() => expect(asked[asked.length - 1]).toContain("source=cloud"));
  const last = asked[asked.length - 1];
  expect(last).toContain("group_by=kind");
  expect(last).not.toContain("model=");
  expect(last).not.toContain("goal=");
  expect(screen.getAllByText("Cloud service").length).toBeGreaterThan(0);
  expect(screen.queryByLabelText("Break down by")).toBeNull();
  expect(screen.queryByText("Costliest tasks")).toBeNull();

  fireEvent.click(screen.getByRole("radio", { name: "All costs" }));
  await waitFor(() => expect(asked[asked.length - 1]).not.toContain("source="));
  expect(asked[asked.length - 1]).toContain("group_by=source");
});

test("the breakdown, bucket and filters are asked for from the URL", async () => {
  const asked = show(COSTS, "/performance?range=7&bucket=week&category=ai&by=model&goal=g-a");
  await screen.findByTestId("cost-stats");
  expect(asked[0]).toContain("bucket=week&group_by=model");
  expect(asked[0]).toContain("goal=g-a");

  fireEvent.change(screen.getByLabelText("Break down by"), { target: { value: "repo" } });
  await waitFor(() => expect(asked[asked.length - 1]).toContain("group_by=repo"));
});

test("a regular user is told they see their own model costs, with no sources and no source breakdown", async () => {
  show({ ...COSTS, scope: "own", group_by: "kind", series: [{ key: "task", usd: [0, 1, 0.25], total: 1.25 }], sources: { model: COSTS.sources.model } }, "/costs?by=run");
  expect((await screen.findByTestId("own-scope")).textContent).toContain("the model cost of your own work");
  expect(screen.queryByTestId("cost-sources")).toBeNull();
  await waitFor(() => expect(screen.queryByRole("radio", { name: "Cloud" })).toBeNull());
  const options = [...(screen.getByLabelText("Break down by") as HTMLSelectElement).options].map((o) => o.value);
  expect(options).toEqual(["model", "run", "goal", "repo", "operator", "label", "outcome"]);
});

test("AI costs are filtered and broken down by the issue's label and by what became of the pull request", async () => {
  const byOutcome = {
    ...COSTS,
    group_by: "outcome",
    series: [{ key: "merged", usd: [0, 3.25, 0], total: 3.25 }, { key: "closed", usd: [0, 0, 1.25], total: 1.25 }],
    choices: { ...COSTS.choices, label: ["bug", "saves"], outcome: ["closed", "merged"] },
  };
  const asked = show(byOutcome, "/performance?category=ai&by=outcome&label=bug&outcome=merged,closed");
  const breakdown = await screen.findByTestId("cost-breakdown");
  expect(asked[0]).toContain("group_by=outcome");
  expect(asked[0]).toContain("label=bug");
  expect(asked[0]).toContain("outcome=merged%2Cclosed");
  expect(breakdown.textContent).toContain("Merged$3.25");
  expect(breakdown.textContent).toContain("Closed without merging$1.25");
  expect(screen.getAllByText("Label").some((e) => e.tagName === "LABEL")).toBe(true);
  expect(screen.getAllByText("Pull request").some((e) => e.tagName === "LABEL")).toBe(true);
  expect(costLabel("", { labels: {}, group_by: "label" })).toBe("No label");
  expect(costLabel("none", { labels: {}, group_by: "outcome" })).toBe("No pull request");

  // A label filter belongs to AI costs: the cloud bill is no issue's.
  fireEvent.click(screen.getByRole("radio", { name: "Cloud" }));
  await waitFor(() => expect(asked[asked.length - 1]).toContain("source=cloud"));
  expect(asked[asked.length - 1]).not.toContain("label=");
  expect(asked[asked.length - 1]).not.toContain("outcome=");
});

test("the cloud category breaks the bill down by the service billed", async () => {
  const cloud = { ...COSTS, group_by: "kind", series: [{ key: "Compute Engine", usd: [1, 1.2, 1.1], total: 3.3 }, { key: "Cloud Run", usd: [0.1, 0.1, 0.1], total: 0.3 }] };
  const asked = show(cloud, "/performance?category=cloud");
  const breakdown = await screen.findByTestId("cost-breakdown");
  expect(asked[0]).toContain("group_by=kind");
  expect(asked[0]).toContain("source=cloud");
  expect(breakdown.textContent).toContain("Compute Engine$3.30");
  expect(screen.getByTestId("cost-legend").textContent).toContain("Cloud Run");
  expect(screen.queryByLabelText("Source")).toBeNull();
});

test("too little spent before shows no percentage, and says why", async () => {
  show({ ...COSTS, previous_total: 0.004, change_percent: null });
  const stats = await screen.findByTestId("cost-stats");
  expect(stats.textContent).toContain("Change from the 3 days before–$0.00 then: too little to compare");
});

test("the key numbers are large, each a median and an average, for the days in view", async () => {
  const all: string[] = [];
  show(COSTS, "/performance?range=7", NUMBERS, all);

  const key = await screen.findByTestId("dollar-hours");
  expect(key.textContent).toContain("Dollar-hours per completed task");
  expect(key.textContent).toContain("1.50median");
  expect(key.textContent).toContain("2.35average");
  expect(key.textContent).toContain("9 merged tasks");
  expect(screen.getByTestId("cost-per-task").textContent).toContain("$4.50median");
  expect(screen.getByTestId("cost-per-pr").textContent).toContain("$6.25average");
  expect(screen.getByTestId("cost-per-goal").textContent).toContain("2 goals");
  expect(screen.getByTestId("key-numbers-note").textContent).toContain("12 tasks finished in this time, 9 merged");
  // The numbers follow the page's own time filter, and sit under it.
  expect(all.some((url) => /^\/v1\/performance\?from=\d{4}-\d\d-\d\d&to=\d{4}-\d\d-\d\d$/.test(url))).toBe(true);
  const range = screen.getByRole("radiogroup", { name: "Range" });
  expect(range.compareDocumentPosition(key) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByTestId("tries-to-green").textContent).toContain("1median");
  expect(screen.getByTestId("tries-to-green").textContent).toContain("1.4average");
  expect(screen.getByTestId("first-try-green").textContent).toContain("67%");
  expect(screen.getByTestId("first-try-green").textContent).toContain("6 of 9 merged tasks");
  expect(screen.getByRole("heading", { name: "Performance" })).toBeTruthy();
});

test("a time with nothing finished says so in each number", async () => {
  const none = { average: null, median: null, count: 0 };
  show(COSTS, "/performance", { ...NUMBERS, tasks: 0, merged: 0, cost_per_task: none, cost_per_pull_request: none, cost_per_goal: none, dollar_hours_per_completed_task: none });

  const key = await screen.findByTestId("dollar-hours");
  expect(key.textContent).toContain("none");
  expect(key.textContent).toContain("No merged tasks in this time");
});

test("Goal Cost Composition shows each stage's average share over the completed goals, with the goals folded under it", async () => {
  show(COSTS);
  const section = await screen.findByTestId("goal-costs");
  expect(section.textContent).toContain("Goal Cost Composition");
  // The ring and its legend were grey on production: the series colors exist only inside what is marked cost-viz.
  expect(section.classList.contains("cost-viz")).toBe(true);
  expect(section.querySelector("style")?.textContent).toContain("--series-1");
  expect(screen.getByTestId("composition-over").textContent).toBe("The average over 1 completed goal in this time.");
  // The stages in the order the work goes through them, each with its share and its average dollars.
  const stages = [...screen.getByTestId("composition").children].map((row) => row.textContent);
  expect(stages).toEqual(["Planning10%$1.00", "Task runs60%$6.00", "Reviews20%$2.00", "Fixes10%$1.00", "Writing pass0%$0.00", "Narratives0%$0.00"]);
  // The goals one by one are folded away until asked for, their columns in the same order.
  const toggle = screen.getByTestId("goal-costs-toggle");
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  const head = [...section.querySelectorAll("th")].map((th) => th.textContent);
  expect(head).toEqual(["Goal", "Total", "Planning", "Task runs", "Reviews", "Fixes", "Writing pass", "Narratives"]);
});

test("the shares count every goal the same, and only completed goals when there are any", () => {
  const goal = (total: number, parts: Record<string, number>, status = "met") => ({ goal_id: "g", title: "", status, total, parts });
  // A $100 goal that is all task runs and a $10 goal that is all planning: half and half, not 91% and 9%.
  const parts = composition([goal(100, { task: 100 }), goal(10, { planning: 10 })]);
  expect(parts.find((p) => p.stage === "task")).toEqual({ stage: "task", share: 0.5, usd: 50 });
  expect(parts.find((p) => p.stage === "planning")).toEqual({ stage: "planning", share: 0.5, usd: 5 });
  expect(composition([])[0].share).toBe(0);
});
