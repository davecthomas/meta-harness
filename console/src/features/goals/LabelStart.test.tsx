import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { NewGoal } from "./NewGoal";
import { theme } from "../../theme";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

test("the draft helper is closed until asked for, picking a label selects its issues, and its draft fills the form", async () => {
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
    if (url.startsWith("/v1/repos/o/a/issues"))
      return respond({
        issues: [
          { ref: "github:o/a#1", number: 1, title: "Crash on save", labels: ["bug"], updated_at: "2", held_by: null },
          { ref: "github:o/a#2", number: 2, title: "Wrong total", labels: ["bug"], updated_at: "1", held_by: "g-9" },
        ],
      });
    if (url.startsWith("/v1/repos/o/b/issues"))
      return respond({
        issues: [{ ref: "github:o/b#5", number: 5, title: "Typo in log", labels: ["bug"], updated_at: "3", held_by: null }],
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

  // Optional and closed: the form below is the goal.
  const helper = await screen.findByRole("button", { name: "Draft from a label (optional)" });
  expect(helper.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("button", { name: "Label bug" })).toBeNull();
  fireEvent.click(helper);

  fireEvent.click(await screen.findByRole("button", { name: "Label bug" }));
  // Picking the label gives the goal that label at once, and the label's repos, since it had none.
  expect((screen.getByRole("combobox", { name: /^Label/ }) as HTMLInputElement).value).toBe("bug");
  expect((screen.getByRole("checkbox", { name: "o/b" }) as HTMLInputElement).checked).toBe(true);
  expect((await screen.findAllByText("Crash on save")).length).toBeGreaterThan(0);
  expect(screen.getByText(/held by goal g-9/)).toBeTruthy();
  // No checkbox chooses issues here: the draft reads every one no other goal holds.
  expect(screen.queryByRole("checkbox", { name: /Keep/ })).toBeNull();
  expect(screen.getByText(/bug: 2 open issues for the goal and the draft, and 1 another goal holds/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Draft the goal" }));

  expect(await screen.findByText("Fix the save bugs")).toBeTruthy();
  expect(screen.getByText(/now and later; Pick issues adds others/)).toBeTruthy();
  expect(drafted).toEqual([{ label: "bug", repos: ["o/a", "o/b"], refs: ["github:o/a#1", "github:o/b#5"] }]);
  await waitFor(() => expect((screen.getByLabelText(/Description/) as HTMLTextAreaElement).value).toBe("Make saving reliable."));
  expect((screen.getByRole("textbox", { name: /^Title/ }) as HTMLInputElement).value).toBe("Fix the save bugs");
  expect((screen.getByRole("combobox", { name: /^Label/ }) as HTMLInputElement).value).toBe("bug");
  expect((screen.getByRole("checkbox", { name: "o/b" }) as HTMLInputElement).checked).toBe(true);

  // The label the draft set selects its issues: the form lists them, and the picker shows them as already in.
  const listed = await screen.findByTestId("label-issues");
  expect(listed.textContent).toContain("2 issues with this label, and 1 more that another goal holds");
  expect(listed.textContent).toContain("Crash on save");
  expect(listed.textContent).toContain("Typo in log");
  expect(listed.textContent).not.toContain("Wrong total");
  fireEvent.click(screen.getByRole("button", { name: "Pick issues" }));
  const locked = (await screen.findByRole("checkbox", { name: "github:o/a#1 is in the goal by its label" })) as HTMLInputElement;
  expect(locked.checked && locked.disabled).toBe(true);
  expect(screen.getAllByText("via label").length).toBe(2);
  expect(screen.getByRole("button", { name: "Use these issues" }).hasAttribute("disabled")).toBe(true);
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

  fireEvent.click(await screen.findByRole("button", { name: "Draft from a label (optional)" }));
  expect(await screen.findByRole("button", { name: "Label docs" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Find a label"), { target: { value: "bu" } });
  expect(screen.queryByRole("button", { name: "Label docs" })).toBeNull();
  expect(screen.getByRole("button", { name: "Label bug" })).toBeTruthy();
});
