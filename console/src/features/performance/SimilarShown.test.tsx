import { cleanup, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { SimilarShown } from "./SimilarShown";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
const show = () => render(<MantineProvider theme={theme}><MemoryRouter><SimilarShown /></MemoryRouter></MantineProvider>);

test("each goal shown is listed with its score and what was said, under the cut-off in force", async () => {
  vi.stubGlobal("fetch", () =>
    json({
      cutoff: 0.7,
      model: "openai:text-embedding-3-small",
      shown: [
        { goal_id: "g-1", title: "Keep task through context compaction", score: 0.912, answer: "same_work", new_goal_id: "", by: "dave", at: "2026-11-26T12:00:00Z" },
        { goal_id: "g-2", title: "", score: 0.74, answer: "not_duplicate", new_goal_id: "", by: "dave", at: "2026-11-26T11:00:00Z" },
      ],
    }));
  show();
  const table = await screen.findByTestId("similar-shown");
  expect(table.textContent).toContain("Goals at 70% similar or more show on New goal.");
  const rows = screen.getAllByRole("row").slice(1).map((row) => row.textContent);
  expect(rows[0]).toContain("Keep task through context compaction");
  expect(rows[0]).toContain("91%");
  expect(rows[0]).toContain("Same work");
  // A goal with no title is named by its id, and still links to its page.
  expect(rows[1]).toContain("g-2");
  expect(rows[1]).toContain("Not a duplicate");
  expect(screen.getByRole("link", { name: "g-2" }).getAttribute("href")).toBe("/goals/g-2");
});

test("with nothing recorded the page shows no table", async () => {
  vi.stubGlobal("fetch", () => json({ cutoff: 0.7, model: "m", shown: [] }));
  show();
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.queryByTestId("similar-shown")).toBeNull();
});
