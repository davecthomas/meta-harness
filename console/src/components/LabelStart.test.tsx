import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { NewGoal } from "../pages/NewGoal";
import { theme } from "../theme";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

test("a label's issues draft a goal, without the issues left out, and the draft fills the form", async () => {
  const drafted: unknown[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/a" }, { name: "o/b" }] });
    if (url === "/v1/labels")
      return respond({
        repos: ["o/a", "o/b"],
        labels: [
          { label: "bug", total: 3, repos: { "o/a": 2, "o/b": 1 } },
          { label: "docs", total: 1, repos: { "o/b": 1 } },
        ],
      });
    if (url === "/v1/repos/o/a/issues?label=bug")
      return respond({
        issues: [
          { ref: "github:o/a#1", number: 1, title: "Crash on save", labels: ["bug"], updated_at: "", held_by: null },
          { ref: "github:o/a#2", number: 2, title: "Wrong total", labels: ["bug"], updated_at: "", held_by: "g-9" },
        ],
      });
    if (url === "/v1/repos/o/b/issues?label=bug")
      return respond({
        issues: [{ ref: "github:o/b#5", number: 5, title: "Typo in log", labels: ["bug"], updated_at: "", held_by: null }],
      });
    if (url === "/v1/goals/draft") {
      drafted.push(JSON.parse(String(init?.body)));
      return respond({
        title: "Fix the save bugs",
        statement: "Make saving reliable.",
        theme: "Saving fails in several ways.",
        modality: "fix",
        model: "claude:claude-haiku-4-5",
        usd_cost: "0.0010",
        label: "bug",
        repos: ["o/a", "o/b"],
        refs: ["github:o/a#1"],
      });
    }
    return respond({ detail: "not stubbed" }, 404);
  });

  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <NewGoal />
      </MemoryRouter>
    </MantineProvider>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "Label bug" }));
  expect(await screen.findByText("Crash on save")).toBeTruthy();
  expect(screen.getByText(/held by goal g-9/)).toBeTruthy();
  const held = screen.getByRole("checkbox", { name: "Keep github:o/a#2" }) as HTMLInputElement;
  expect(held.checked).toBe(false);
  expect(held.disabled).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: "Keep github:o/b#5" }));
  fireEvent.click(screen.getByRole("button", { name: "Draft the goal" }));

  expect(await screen.findByText("Fix the save bugs")).toBeTruthy();
  expect(drafted).toEqual([{ label: "bug", repos: ["o/a", "o/b"], refs: ["github:o/a#1"] }]);
  await waitFor(() => expect((screen.getByLabelText(/Statement/) as HTMLTextAreaElement).value).toBe("Make saving reliable."));
  expect((screen.getByRole("combobox", { name: /^Label/ }) as HTMLInputElement).value).toBe("bug");
  expect((screen.getByRole("checkbox", { name: "o/b" }) as HTMLInputElement).checked).toBe(true);
});

test("the label list filters by text and by repo", async () => {
  vi.stubGlobal("fetch", (url: string) => {
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/a" }] });
    if (url === "/v1/labels")
      return respond({
        repos: ["o/a", "o/b"],
        labels: [
          { label: "bug", total: 3, repos: { "o/a": 2, "o/b": 1 } },
          { label: "docs", total: 1, repos: { "o/b": 1 } },
        ],
      });
    return respond({ detail: "not stubbed" }, 404);
  });

  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <NewGoal />
      </MemoryRouter>
    </MantineProvider>,
  );

  expect(await screen.findByRole("button", { name: "Label docs" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Find a label"), { target: { value: "bu" } });
  expect(screen.queryByRole("button", { name: "Label docs" })).toBeNull();
  expect(screen.getByRole("button", { name: "Label bug" })).toBeTruthy();
});
