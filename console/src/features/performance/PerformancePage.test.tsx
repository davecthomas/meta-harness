import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { Costs } from "../../api";
import { theme } from "../../theme";
import { axisTicks, bucketLabel, seriesColor } from "./CostChart";
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

function show(costs: Costs, path = "/performance") {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
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
  expect(costLabel("cloud", COSTS)).toBe("Google Cloud");
  expect(costLabel("task", { labels: {}, group_by: "model" })).toBe("task");
});

test("an admin sees the totals, the chart with its trend, where the money went and each source", async () => {
  const asked = show(COSTS);
  const stats = await screen.findByTestId("cost-stats");
  expect(stats.textContent).toContain("$8.71");
  expect(stats.textContent).toContain("$2.90");
  expect(stats.textContent).toContain("+117.8%");
  expect(stats.textContent).toContain("$105.90");
  expect(stats.textContent).toContain("Rising $0.63 a day");
  expect(asked[0]).toMatch(/^\/v1\/costs\?from=\d{4}-\d\d-\d\d&to=\d{4}-\d\d-\d\d&bucket=day&group_by=source$/);

  const chart = screen.getByTestId("cost-chart");
  expect(chart.querySelectorAll("path[data-series]").length).toBe(3);
  expect(screen.getByTestId("trend-line")).toBeTruthy();
  expect(screen.getByTestId("cost-legend").textContent).toBe("ModelsGoogle CloudTrend");
  const breakdown = screen.getByTestId("cost-breakdown").textContent ?? "";
  expect(breakdown).toContain("Models$4.51 52%");
  expect(breakdown).toContain("Google Cloud$4.20 48%");
  expect(screen.getByRole("link", { name: "Fix it" }).getAttribute("href")).toBe("/tasks/t-1?tab=spend");
  expect(screen.getByTestId("cost-sources").textContent).toContain("Not connected: turn on the billing export");
  expect(screen.queryByTestId("own-scope")).toBeNull();

  // Hovering a bucket lists every series in it.
  fireEvent.pointerEnter(chart.querySelectorAll("rect")[1]);
  const tip = await screen.findByRole("tooltip");
  expect(tip.textContent).toContain("Nov 10");
  expect(tip.textContent).toContain("$7.45");
  expect(tip.textContent).toContain("$4.20Google Cloud");
});

test("the breakdown, bucket and filters are asked for from the URL", async () => {
  const asked = show(COSTS, "/performance?range=7&bucket=week&by=model&source=model&goal=g-a");
  await screen.findByTestId("cost-stats");
  expect(asked[0]).toContain("bucket=week&group_by=model");
  expect(asked[0]).toContain("source=model");
  expect(asked[0]).toContain("goal=g-a");

  fireEvent.change(screen.getByLabelText("Break down by"), { target: { value: "repo" } });
  await waitFor(() => expect(asked[asked.length - 1]).toContain("group_by=repo"));
});

test("a regular user is told they see their own model costs, with no sources and no source breakdown", async () => {
  show({ ...COSTS, scope: "own", group_by: "kind", series: [{ key: "task", usd: [0, 1, 0.25], total: 1.25 }], sources: { model: COSTS.sources.model } }, "/performance?by=kind");
  expect((await screen.findByTestId("own-scope")).textContent).toContain("the model cost of your own work");
  expect(screen.queryByTestId("cost-sources")).toBeNull();
  const options = [...(screen.getByLabelText("Break down by") as HTMLSelectElement).options].map((o) => o.value);
  expect(options).toEqual(["kind", "goal", "repo", "model"]);
});

test("the Google Cloud service breakdown asks for the cloud source by the service billed", async () => {
  const cloud = { ...COSTS, group_by: "kind", series: [{ key: "Compute Engine", usd: [1, 1.2, 1.1], total: 3.3 }, { key: "Cloud Run", usd: [0.1, 0.1, 0.1], total: 0.3 }] };
  const asked = show(cloud, "/performance?by=service");
  const breakdown = await screen.findByTestId("cost-breakdown");
  expect(asked[0]).toContain("group_by=kind");
  expect(asked[0]).toContain("source=cloud");
  expect(breakdown.textContent).toContain("Compute Engine$3.30");
  expect(screen.getByTestId("cost-legend").textContent).toContain("Cloud Run");
  expect(screen.queryByLabelText("Source")).toBeNull();
});
