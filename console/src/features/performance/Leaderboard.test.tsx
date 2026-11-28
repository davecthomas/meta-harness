import { fireEvent, render, screen, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme";
import type { Board } from "./api";
import { BoardChart, RankCard, along, decades } from "./Leaderboard";

const BOARD: Board = {
  metric: "dollar-hours per successful task",
  rank: 3,
  of: 4,
  multiple_of_best: 15.62,
  tasks: 14,
  rows: [
    { rank: 1, harness: "cursor", model: "fast", value: 0.16, muxxy: false },
    { rank: 2, harness: "pi", model: "opus", value: 1.88, muxxy: false },
    { rank: 3, harness: "Muxxy", model: "", value: 2.5, muxxy: true },
    { rank: 4, harness: "claude", model: "opus", value: 596.82, muxxy: false },
  ],
};

const show = (ui: React.ReactElement) => render(<MantineProvider theme={theme}>{ui}</MantineProvider>);

test("the rank is a big number, and the board opens with Muxxy in its place among the others", async () => {
  show(<RankCard board={BOARD} />);

  const card = screen.getByTestId("board-rank");
  expect(card.textContent).toContain("3 of 4");
  expect(card.textContent).toContain("2.50, 16 times the cheapest");
  fireEvent.click(within(card).getByRole("button", { name: "Show the leaderboard" }));

  const table = await screen.findByTestId("board-table");
  const rows = within(table).getAllByRole("row").slice(1).map((r) => r.textContent);
  expect(rows).toEqual(["1cursor · fast0.16", "2pi · opus1.88", "3Muxxy2.50", "4claude · opus597"]);
  expect(within(table).getByTestId("board-row-muxxy").textContent).toBe("3Muxxy2.50");
  // Said with the board, wherever it is shown: the figures are not over the same tasks.
  expect(screen.getAllByTestId("board-candid").at(-1)?.textContent).toContain("Muxxy's is over its own 14 tasks in this time, which are different tasks");
});

test("the chart is logarithmic, so a figure a thousand times another still fits on one line", () => {
  const ticks = decades(BOARD.rows.map((r) => r.value));
  expect(ticks).toEqual([0.1, 1, 10, 100, 1000]);
  // Equal steps are equal multiples: 1 is a quarter of the way from 0.1 to 1,000, and 100 three quarters.
  expect(along(1, ticks)).toBeCloseTo(0.25);
  expect(along(100, ticks)).toBeCloseTo(0.75);
  expect(along(0.16, ticks)).toBeLessThan(along(2.5, ticks));

  show(<BoardChart board={BOARD} />);
  const chart = screen.getByTestId("board-chart");
  expect(within(chart).getAllByRole("listitem")).toHaveLength(4);
  expect(within(chart).getByTestId("board-bar-muxxy").textContent).toContain("Muxxy");
  expect(chart.textContent).toContain("Lower is better. Logarithmic: each mark is ten times the one before.");
});

test("with no figure to place, the rank says so and the board is still the measured pairs", () => {
  show(<RankCard board={{ ...BOARD, rank: null, of: 3, multiple_of_best: null, tasks: 0, rows: BOARD.rows.filter((r) => !r.muxxy) }} />);
  expect(screen.getByTestId("board-rank").textContent).toContain("No finished task in this time to rank");
});
