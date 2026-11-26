import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { ReviewersPage } from "./ReviewersPage";

afterEach(() => vi.unstubAllGlobals());

const ROW = {
  reviewer: "harness:pi-review/claude:claude-opus-5-5",
  rounds: 4,
  findings: 6,
  fixed: 3,
  not_fixed: 0,
  dismissed: 1,
  never_judged: 2,
  cost_usd: "1.60",
};

test("each reviewer shows its rounds, what became of its findings, and what its reviews cost, over the days picked", async () => {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    return Promise.resolve(new Response(JSON.stringify({ since: "", until: "", reviewers: [ROW] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <ReviewersPage />
      </MemoryRouter>
    </MantineProvider>,
  );
  const row = await screen.findByTestId("reviewer");
  expect(within(row).getByText("pi-review · claude-opus-5-5")).toBeTruthy();
  // Fixed of judged: 3 fixed of 4 judged (fixed, not fixed and dismissed); never judged is left out.
  expect(row.textContent).toContain("75%");
  expect(row.textContent).toContain("$1.60");
  expect(asked[0]).toBe("/v1/reviewers?days=30");

  fireEvent.click(screen.getByText("7 days"));
  await waitFor(() => expect(asked.at(-1)).toBe("/v1/reviewers?days=7"));
});

test("a time with no review round says so", async () => {
  vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify({ since: "", until: "", reviewers: [] }), { status: 200, headers: { "Content-Type": "application/json" } })));
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <ReviewersPage />
      </MemoryRouter>
    </MantineProvider>,
  );
  expect(await screen.findByText(/No review rounds in this period/)).toBeTruthy();
});
