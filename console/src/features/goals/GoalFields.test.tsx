import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { GoalPage } from "./GoalPage";
import { theme } from "../../theme";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

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
function renderGoal(start: Record<string, unknown>, refuse?: string) {
  let current = start;
  const patched: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
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
  expect(within(await screen.findByRole("button", { name: "Edit budget" })).getByText("None")).toBeTruthy();
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

  fireEvent.click(await screen.findByRole("button", { name: "Edit label" }));
  let label = screen.getByRole("combobox", { name: "Label" });
  fireEvent.change(label, { target: { value: "" } });
  fireEvent.keyDown(label, { key: "Enter" });
  expect((await screen.findByRole("alert")).textContent).toContain("needs a label");
  expect(patched).toEqual([]);
  cleanup();

  const picked = renderGoal(goal("active", { issues: [{ ...swept, picked: true }] }));
  fireEvent.click(await screen.findByRole("button", { name: "Edit label" }));
  label = screen.getByRole("combobox", { name: "Label" });
  fireEvent.change(label, { target: { value: "" } });
  fireEvent.keyDown(label, { key: "Enter" });
  await waitFor(() => expect(picked).toEqual([{ label: null }]));
});

test("auto-merge is a switch that saves when flipped", async () => {
  const patched = renderGoal(goal());

  fireEvent.click(await screen.findByRole("switch", { name: "Auto-merge" }));

  await waitFor(() => expect(patched).toEqual([{ auto_merge: true }]));
  expect(await screen.findByText("On")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Turn (on|off)/ })).toBeNull();
});

test.each(["met", "canceled"])("a %s goal shows its fields read-only", async (status) => {
  renderGoal(goal(status));

  expect(await screen.findByRole("button", { name: "Reopen goal" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
  expect(screen.queryByRole("switch")).toBeNull();
  expect(screen.getByText("Off: pull requests wait for you")).toBeTruthy();
});
