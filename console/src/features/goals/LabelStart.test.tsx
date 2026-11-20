import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { LabelStart } from "./LabelStart";
import { NewGoal } from "./NewGoal";
import { theme } from "../../theme";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

test("the draft helper is closed until asked for, lists a label's issues read-only, and its draft fills the form", async () => {
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
  const helper = await screen.findByRole("button", { name: "Draft from labels (optional)" });
  expect(helper.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("button", { name: "Label bug" })).toBeNull();
  fireEvent.click(helper);

  fireEvent.click(await screen.findByRole("button", { name: "Label bug" }));
  expect(await screen.findByText("Crash on save")).toBeTruthy();
  expect(screen.getByText(/held by goal g-9/)).toBeTruthy();
  // No checkbox chooses issues here: the draft reads every one no other goal holds.
  expect(screen.queryByRole("checkbox", { name: /Keep/ })).toBeNull();
  expect(screen.getByText(/bug: 2 open issues for the draft, and 1 another goal holds/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Draft the goal" }));

  expect(await screen.findByText("Fix the save bugs")).toBeTruthy();
  expect(drafted).toEqual([{ labels: ["bug"], repos: ["o/a", "o/b"], refs: ["github:o/a#1", "github:o/b#5"] }]);
  await waitFor(() => expect((screen.getByLabelText(/Description/) as HTMLTextAreaElement).value).toBe("Make saving reliable."));
  expect((screen.getByRole("textbox", { name: /^Title/ }) as HTMLInputElement).value).toBe("Fix the save bugs");
  expect((screen.getByRole("combobox", { name: /^Label/ }) as HTMLInputElement).value).toBe("bug");
  expect((screen.getByRole("checkbox", { name: "o/b" }) as HTMLInputElement).checked).toBe(true);

  // The label the draft set selects its issues: the form lists them, and the picker shows them as already in.
  const listed = await screen.findByTestId("label-issues");
  expect(listed.textContent).toContain("2 open issues with this label, and 1 more that another goal holds");
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

  fireEvent.click(await screen.findByRole("button", { name: "Draft from labels (optional)" }));
  expect(await screen.findByRole("button", { name: "Label docs" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Find a label"), { target: { value: "bu" } });
  expect(screen.queryByRole("button", { name: "Label docs" })).toBeNull();
  expect(screen.getByRole("button", { name: "Label bug" })).toBeTruthy();
});

test("an answer for a project no longer asked about, even a refusal arriving last, is dropped", async () => {
  const answers: Record<string, (r: Response) => void> = {};
  vi.stubGlobal("fetch", (url: string) => new Promise<Response>((done) => { answers[decodeURIComponent(url).includes('"AB"') ? "AB" : "V"] = done; }));
  const view = (project: string) => (
    <MantineProvider theme={theme}><LabelStart onDraft={() => undefined} source={{ tracker: "jira", settings: { project } }} /></MantineProvider>
  );
  const { rerender } = render(view("V"));
  fireEvent.click(screen.getByRole("button", { name: "Draft from labels (optional)" }));
  rerender(view("AB"));
  await waitFor(() => expect(Object.keys(answers).sort()).toEqual(["AB", "V"]));

  answers.AB(new Response(JSON.stringify({ repos: ["o/a"], labels: [{ label: "bug", total: 1, repos: { "o/a": 1 } }] }), { status: 200 }));
  expect(await screen.findByText("bug")).toBeTruthy();
  answers.V(new Response(JSON.stringify({ detail: "names its project by its key" }), { status: 422 }));
  await new Promise((r) => setTimeout(r, 20));

  expect(screen.queryByText("names its project by its key")).toBeNull();
});

test("a draft from several labels reads each issue once and fills the form with them as picked issues", async () => {
  const drafted: Record<string, unknown>[] = [];
  const issue = (n: number, title: string) => ({ ref: `github:o/a#${n}`, number: n, title, labels: [], updated_at: "", held_by: null });
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/a" }] });
    if (url === "/v1/labels") return respond({ repos: ["o/a"], labels: [{ label: "bug", total: 2, repos: { "o/a": 2 } }, { label: "ux", total: 2, repos: { "o/a": 2 } }] });
    if (url === "/v1/repos/o/a/issues?label=bug") return respond({ issues: [issue(1, "Crash on save"), issue(2, "Both labels")] });
    if (url === "/v1/repos/o/a/issues?label=ux") return respond({ issues: [issue(2, "Both labels"), issue(3, "Cramped table")] });
    if (url.startsWith("/v1/repos/o/a/issues")) return respond({ issues: [issue(1, "Crash on save"), issue(2, "Both labels"), issue(3, "Cramped table")], total: 3 });
    if (url === "/v1/goals/draft") {
      drafted.push(JSON.parse(String(init?.body)));
      return respond({ title: "Fix the console", statement: "Bugs and rough edges.", theme: "Th", modality: "fix", model: "m", usd_cost: "0.001", label: null, labels: ["bug", "ux"], repos: ["o/a"], refs: ["github:o/a#1", "github:o/a#2", "github:o/a#3"] });
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
  fireEvent.click(await screen.findByRole("button", { name: "Draft from labels (optional)" }));

  fireEvent.click(await screen.findByRole("button", { name: "Label bug" }));
  fireEvent.click(screen.getByRole("button", { name: "Label ux" }));
  expect(await screen.findByText(/bug, ux: 3 open issues for the draft/)).toBeTruthy();
  expect(screen.getAllByText("Both labels")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Label ux" }).getAttribute("aria-pressed")).toBe("true");

  // Taking a label out again reads only what is left.
  fireEvent.click(screen.getByRole("button", { name: "Label ux" }));
  expect(await screen.findByText(/^bug: 2 open issues for the draft/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Label ux" }));
  await screen.findByText(/bug, ux: 3 open issues for the draft/);

  fireEvent.click(screen.getByRole("button", { name: "Draft the goal" }));
  expect(await screen.findByText(/A goal takes one label, so with several it holds these issues/)).toBeTruthy();
  expect(drafted).toEqual([{ labels: ["bug", "ux"], repos: ["o/a"], refs: ["github:o/a#1", "github:o/a#2", "github:o/a#3"] }]);
  expect((screen.getByRole("combobox", { name: /^Label/ }) as HTMLInputElement).value).toBe("");
  expect(await screen.findByText(/3 picked by hand/)).toBeTruthy();
});
