import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { IssuesPicker } from "./IssuesPicker";

afterEach(() => vi.unstubAllGlobals());

function respond(body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
}

const ISSUES = [
  { ref: "github:o/a#1", number: 1, title: "Crash on save", labels: ["bug"], updated_at: "2026-11-09T10:00:00Z", held_by: null },
  { ref: "github:o/a#2", number: 2, title: "Slow load", labels: [], updated_at: "2026-11-09T09:00:00Z", held_by: "g-other" },
  { ref: "github:o/a#3", number: 3, title: "Typo", labels: [], updated_at: "2026-11-09T08:00:00Z", held_by: null },
];

test("the picker lists a repo's open issues, keeps held ones out, and picks the rest a page at a time", async () => {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    return respond({ issues: ISSUES, total: 3, next_cursor: null });
  });
  const picked: string[][] = [];

  render(
    <MantineProvider theme={theme}>
      <IssuesPicker opened onClose={() => undefined} repos={["o/a"]} action="Add to goal" onPick={(refs) => void picked.push(refs)} />
    </MantineProvider>,
  );

  expect(await screen.findByText("Crash on save")).toBeTruthy();
  expect((screen.getByLabelText("Pick github:o/a#2") as HTMLInputElement).disabled).toBe(true);
  expect(screen.getByText("g-other")).toBeTruthy();
  fireEvent.click(screen.getByLabelText("Select every issue on this page"));
  expect(screen.getByText("2 selected")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Add to goal" }));

  await waitFor(() => expect(picked).toEqual([["github:o/a#1", "github:o/a#3"]]));
  fireEvent.change(screen.getByLabelText("Search"), { target: { value: "crash" } });
  await waitFor(() => expect(asked.some((u) => u.includes("q=crash"))).toBe(true));
});
