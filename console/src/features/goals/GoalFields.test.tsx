import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { GoalPage } from "./GoalPage";
import { theme } from "../../theme";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

// What the page asked the trackers for, in the test that runs now.
const trackerAsks: string[] = [];

afterEach(() => {
  vi.unstubAllGlobals();
  trackerAsks.length = 0;
});

function goal(status = "active", more: Record<string, unknown> = {}) {
  return {
    id: "g-1",
    title: "Fix issue 55",
    statement: "Close #55.",
    repos: ["o/r"],
    label: "mh:fix",
    modality: "fix",
    status,
    auto_merge: false,
    max_usd: "10",
    max_parallel: null,
    priority: 0,
    created_by: "dave",
    created_at: "2026-11-08T20:40:00Z",
    started_at: "2026-11-08T20:41:00Z",
    closed_at: ["met", "canceled"].includes(status) ? "2026-11-09T10:00:00Z" : null,
    issues: [],
    ...more,
  };
}

// The goal page over a fake API that records each PATCH body. refuse: the reason a PATCH is refused, if any.
// people: what the tracker lists for the assignee setting, or the reason it cannot list them. epics: the same
// for the parent setting.
type Listed = { value: string; label: string }[] | string;
function renderGoal(start: Record<string, unknown>, refuse?: string, people?: Listed, epics?: Listed) {
  let current = start;
  const patched: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url.startsWith("/v1/trackers/")) {
      trackerAsks.push(url);
      if (url.includes("/choices/parent")) return typeof epics === "string" ? respond({ detail: epics }, 422) : respond({ choices: epics ?? [] });
      return typeof people === "string" ? respond({ detail: people }, 422) : respond({ choices: people ?? [] });
    }
    if (init?.method === "PATCH") {
      const body = JSON.parse(String(init.body));
      patched.push(body);
      if (refuse) return respond({ detail: refuse }, 409);
      current = { ...current, ...body };
      return respond(current);
    }
    if (url.startsWith("/v1/tasks")) return respond({ tasks: [], total: 0 });
    if (url === "/v1/limits") return respond({ max_parallel: 4, max_sandboxes: 8 });
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/r" }, { name: "o/s" }] });
    if (url.startsWith("/v1/labels")) return respond({ repos: ["o/r"], labels: [] });
    return respond(current);
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/goals/g-1"]}>
        <Routes>
          <Route path="/goals/:goalId" element={<GoalPage />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
  return patched;
}

test("a cleared budget saves on leaving the field, sends only the budget, and shows None", async () => {
  const patched = renderGoal(goal());

  fireEvent.click(await screen.findByRole("button", { name: "Edit budget" }));
  const input = screen.getByLabelText("Budget");
  fireEvent.change(input, { target: { value: "" } });
  fireEvent.blur(input);

  await waitFor(() => expect(patched).toEqual([{ max_usd: null }]));
  // Back to its value, with its pencil beside it.
  expect(await screen.findByRole("button", { name: "Edit budget" })).toBeTruthy();
  expect(screen.getByText("None")).toBeTruthy();
});

test("Escape cancels an edit and sends nothing", async () => {
  const patched = renderGoal(goal());

  fireEvent.click(await screen.findByRole("button", { name: "Edit priority" }));
  const input = screen.getByLabelText("Priority");
  fireEvent.change(input, { target: { value: "5" } });
  fireEvent.keyDown(input, { key: "Escape" });

  expect(screen.queryByLabelText("Priority")).toBeNull();
  expect(patched).toEqual([]);
});

test("a priority is saved with Enter, and a multi-line description with Ctrl+Enter", async () => {
  const patched = renderGoal(goal());

  fireEvent.click(await screen.findByRole("button", { name: "Edit priority" }));
  const priority = screen.getByLabelText("Priority");
  fireEvent.change(priority, { target: { value: "3" } });
  fireEvent.keyDown(priority, { key: "Enter" });
  await waitFor(() => expect(patched).toEqual([{ priority: 3 }]));

  fireEvent.click(screen.getByRole("button", { name: "Edit description" }));
  const text = screen.getByLabelText("Description");
  fireEvent.change(text, { target: { value: "Close #55.\nAnd #56." } });
  fireEvent.keyDown(text, { key: "Enter" });
  expect(patched).toHaveLength(1);
  fireEvent.keyDown(text, { key: "Enter", ctrlKey: true });

  await waitFor(() => expect(patched).toEqual([{ priority: 3 }, { statement: "Close #55.\nAnd #56." }]));
});

test("a refused save keeps the input open with the API's reason under it", async () => {
  renderGoal(goal(), "a goal with no picked issues needs a label: pick its issues first");

  fireEvent.click(await screen.findByRole("button", { name: "Edit parallel tasks" }));
  const input = screen.getByLabelText("Parallel tasks");
  fireEvent.change(input, { target: { value: "2" } });
  fireEvent.keyDown(input, { key: "Enter" });

  expect((await screen.findByRole("alert")).textContent).toContain("needs a label");
  expect(screen.getByLabelText("Parallel tasks")).toBeTruthy();
});

test("a label is not cleared on a goal with no picked issues, and repos are picked as checkboxes", async () => {
  const patched = renderGoal(goal());

  fireEvent.click(await screen.findByRole("button", { name: "Edit label" }));
  const label = screen.getByRole("combobox", { name: "Label" });
  fireEvent.change(label, { target: { value: "" } });
  fireEvent.keyDown(label, { key: "Enter" });
  expect((await screen.findByRole("alert")).textContent).toContain("needs a label");
  fireEvent.keyDown(label, { key: "Escape" });

  fireEvent.click(screen.getByRole("button", { name: "Edit repos" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "o/s" }));
  fireEvent.keyDown(screen.getByRole("checkbox", { name: "o/s" }), { key: "Enter" });

  await waitFor(() => expect(patched).toEqual([{ repos: ["o/r", "o/s"] }]));
});

test("a label its sweep's issues came from is kept, and one with a picked issue is cleared", async () => {
  const swept = { ref: "github:o/r#7", repo: "o/r", task_id: null, state: "open", depends_on: [], refused_reason: null, picked: false };
  const patched = renderGoal(goal("active", { issues: [swept] }));

  // A goal with issues folds its settings into one line; they open to be edited.
  expect((await screen.findByTestId("goal-settings-summary")).textContent).toBe("Settings · label mh:fix · o/r · budget $10.00 · take the clear steps");
  expect(screen.queryByRole("button", { name: "Edit label" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Show and edit settings" }));
  fireEvent.click(await screen.findByRole("button", { name: "Edit label" }));
  let label = screen.getByRole("combobox", { name: "Label" });
  fireEvent.change(label, { target: { value: "" } });
  fireEvent.keyDown(label, { key: "Enter" });
  expect((await screen.findByRole("alert")).textContent).toContain("needs a label");
  expect(patched).toEqual([]);
  cleanup();

  const picked = renderGoal(goal("active", { issues: [{ ...swept, picked: true }] }));
  fireEvent.click(await screen.findByRole("button", { name: "Show and edit settings" }));
  fireEvent.click(await screen.findByRole("button", { name: "Edit label" }));
  label = screen.getByRole("combobox", { name: "Label" });
  fireEvent.change(label, { target: { value: "" } });
  fireEvent.keyDown(label, { key: "Enter" });
  await waitFor(() => expect(picked).toEqual([{ label: null }]));
});

test("how much is done without asking is one choice of three, saved when picked, each saying what it does", async () => {
  const patched = renderGoal(goal());

  const choice = await screen.findByTestId("autonomy");
  expect(choice.textContent).toContain("It resolves conflicts and retries by itself. A reviewed pull request waits for you to merge.");
  fireEvent.click(screen.getByRole("radio", { name: "Ask me first" }));
  await waitFor(() => expect(patched).toEqual([{ autonomy: "ask" }]));
  fireEvent.click(screen.getByRole("radio", { name: "Also merge" }));
  await waitFor(() => expect(patched).toEqual([{ autonomy: "ask" }, { autonomy: "full" }]));
});

test("the goal's assignee is named as the tracker lists them, from the goal's project and repos", async () => {
  const people = [{ value: "acc-alice", label: "Alice Ng (alice@example.com)" }];
  renderGoal(goal("active", { tracker: "jira", tracker_settings: { project: "VL", assignee: "acc-alice" } }), undefined, people);

  const field = await screen.findByTestId("goal-assignee");
  await waitFor(() => expect(field.textContent).toContain("Alice Ng (alice@example.com)"));
  expect(field.textContent).not.toContain("not one of the people");
  expect(trackerAsks).toEqual(["/v1/trackers/jira/choices/assignee?project=VL&repos=o%2Fr"]);
  // The tracker line no longer repeats the raw id.
  expect(screen.queryByText(/assignee acc-alice/)).toBeNull();
});

test("an assignee the tracker no longer lists is marked, and one it cannot list is still shown", async () => {
  renderGoal(goal("active", { tracker_settings: { assignee: "octocat" } }), undefined, [{ value: "zed", label: "zed" }]);
  expect((await screen.findByText(/octocat is not one of the people the tracker can assign now/)).textContent).toContain("start a new goal that names another assignee");
  cleanup();

  renderGoal(goal("active", { tracker_settings: { assignee: "octocat" } }), undefined, "GitHub could not be reached");
  expect((await screen.findByText(/The tracker's people could not be read/)).textContent).toContain("GitHub could not be reached");
  expect(screen.getByTestId("goal-assignee").textContent).toContain("octocat");
  cleanup();

  // None named: no list is asked for.
  renderGoal(goal());
  expect(await screen.findByText(/^None named: each issue goes to the goal's owner/)).toBeTruthy();
});

test("a Jira goal's parent is named as the project's epics list it", async () => {
  const epics = [{ value: "jira:VL-100", label: "VL-100: Checkout" }];
  renderGoal(goal("active", { tracker: "jira", tracker_settings: { project: "VL", parent: "jira:VL-100" } }), undefined, [], epics);

  const field = await screen.findByTestId("goal-parent");
  await waitFor(() => expect(field.textContent).toContain("VL-100: Checkout"));
  expect(field.textContent).toContain("The issues its plan creates go under it.");
  expect(field.textContent).not.toContain("not one of the project's open epics");
  expect(trackerAsks).toContain("/v1/trackers/jira/choices/parent?project=VL");
  // The tracker line no longer repeats the raw ref.
  expect(screen.queryByText(/parent jira:VL-100/)).toBeNull();
});

test("a parent that is no longer an open epic is marked, one that cannot be read is still shown, and none says so", async () => {
  const jira = (settings: Record<string, string>) => goal("active", { tracker: "jira", tracker_settings: { project: "VL", ...settings } });
  renderGoal(jira({ parent: "jira:VL-9" }), undefined, [], [{ value: "jira:VL-100", label: "VL-100: Checkout" }]);
  expect((await screen.findByText(/jira:VL-9 is not one of the project's open epics now/)).textContent).toContain("set each issue's parent in Jira");
  cleanup();

  renderGoal(jira({ parent: "jira:VL-9" }), undefined, [], "Jira could not be reached");
  expect((await screen.findByText(/The project's epics could not be read/)).textContent).toContain("Jira could not be reached");
  expect(screen.getByTestId("goal-parent").textContent).toContain("VL-9");
  cleanup();
  trackerAsks.length = 0;

  renderGoal(jira({}));
  expect(await screen.findByText("None: the issues its plan creates have no parent.")).toBeTruthy();
  expect(trackerAsks.filter((url) => url.includes("/choices/parent"))).toEqual([]);
});

test("a GitHub goal shows no parent setting", async () => {
  renderGoal(goal("active", { tracker: "github" }));

  expect(await screen.findByText(/^None named: each issue goes to the goal's owner/)).toBeTruthy();
  expect(screen.queryByText("Parent")).toBeNull();
  expect(screen.queryByTestId("goal-parent")).toBeNull();
  expect(trackerAsks.filter((url) => url.includes("/choices/parent"))).toEqual([]);
});

test.each(["met", "canceled"])("a %s goal shows its fields read-only", async (status) => {
  renderGoal(goal(status));

  expect(await screen.findByRole("button", { name: "Reopen goal" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
  expect(screen.queryByRole("switch")).toBeNull();
  expect(screen.queryByRole("radio")).toBeNull();
  expect(screen.getByText(/^Take the clear steps: It resolves conflicts and retries by itself/)).toBeTruthy();
});
