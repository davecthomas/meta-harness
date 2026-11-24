import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, RouterProvider, Routes, createMemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { NewGoal } from "./NewGoal";
import { theme } from "../../theme";

// The New goal page, one decision at a time (docs/specs/new-goal-progressive.md).

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

const BASE = { description: "", settings: [], credentials: [], missing_credentials: [], signed_in: true, configured: true, writes_progress: true, issue_url_prefix: null, goal_settings: [], goal_defaults: {} };
const TRACKERS = { default: "github", trackers: [{ ...BASE, scheme: "github", title: "GitHub Issues" }, { ...BASE, scheme: "mh", title: "None: no issue tracking", built_in: true }] };

// A fake API over one repo. asked: every call, by method and address; created: each goal posted.
function stub(created: Record<string, unknown>[], asked: string[] = [], extra: (url: string, init?: RequestInit) => Promise<Response> | null = () => null) {
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    asked.push(`${init?.method ?? "GET"} ${url}`);
    const answer = extra(url, init);
    if (answer) return answer;
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/a" }] });
    if (url === "/v1/trackers") return respond(TRACKERS);
    if (url.startsWith("/v1/labels")) return respond({ repos: ["o/a"], labels: [{ label: "bug", total: 2, repos: { "o/a": 2 } }] });
    if (url === "/v1/goals/title") return respond({ title: "Add a CSV export" });
    if (url === "/v1/goals/draft")
      return respond({ title: "Fix the save bugs", statement: "Fix the two save bugs users hit.", theme: "", modality: "fix", model: "m", usd_cost: "0", label: "bug", labels: ["bug"], repos: ["o/a"], refs: [] });
    if (url === "/v1/goals" && init?.method === "POST") {
      created.push(JSON.parse(String(init.body)));
      return respond({ id: "g-1" }, 201);
    }
    return respond({ detail: "not stubbed" }, 404);
  });
}

function show() {
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/goals/new"]}>
        <Routes>
          <Route path="/goals/new" element={<NewGoal />} />
          <Route path="/goals/:goalId" element={<p>Goal page</p>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
}

async function pick(source: "issues" | "spec" | "describe") {
  const card = await screen.findByTestId(`source-${source}`);
  await waitFor(() => expect(card.hasAttribute("disabled")).toBe(false));
  fireEvent.click(card);
}

async function next(panel: number) {
  const button = within(screen.getByTestId(`step-${panel}`)).getByRole("button", { name: "Continue" });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(button);
}

test("only the first question shows at first, and each answer opens the next", async () => {
  stub([]);
  show();

  expect(await screen.findByText("Where does the work come from?")).toBeTruthy();
  expect(screen.queryByTestId("step-2")).toBeNull();
  expect(screen.queryByLabelText(/Description/)).toBeNull();
  expect(screen.queryByRole("button", { name: /Save/ })).toBeNull();
  expect(screen.getByRole("heading", { name: "New goal" })).toBeTruthy();

  await pick("describe");
  // The first panel folds to its answer, with a pencil; the second opens.
  expect(screen.getByTestId("answer-1").textContent).toBe("I'll describe it");
  expect(screen.getByTestId("step-2").getAttribute("data-open")).toBe("true");
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  expect(screen.queryByTestId("step-3")).toBeNull();
});

test("a described goal gets a written title as the page's title, and is saved to be planned", async () => {
  const created: Record<string, unknown>[] = [];
  stub(created);
  show();
  await pick("describe");
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  await next(2);

  fireEvent.change(screen.getByTestId("description"), { target: { value: "Add CSV export" } });
  await next(3);

  // The title is written from the description and becomes the page's title, with a pencil to change it.
  expect(await screen.findByRole("heading", { name: "Add a CSV export" })).toBeTruthy();
  expect(screen.getByText("How much it does without asking")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Save and plan" }));

  expect(await screen.findByText("Goal page")).toBeTruthy();
  expect(created[0]).toMatchObject({
    title: "Add a CSV export",
    statement: "Add CSV export",
    repos: ["o/a"],
    modality: "feature",
    autonomy: "standard",
    start: true,
  });
});

test("a goal on issues that exist has its description and title written from them, and runs them as they are", async () => {
  const created: Record<string, unknown>[] = [];
  const asked: string[] = [];
  stub(created, asked);
  show();
  await pick("issues");
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  await next(2);

  fireEvent.change(screen.getByRole("combobox", { name: /^Label/ }), { target: { value: "bug" } });
  await next(3);

  // The system writes the description: the operator typed none.
  expect(await screen.findByRole("heading", { name: "Fix the save bugs" })).toBeTruthy();
  expect(asked).toContain("POST /v1/goals/draft");
  fireEvent.click(await screen.findByRole("button", { name: "Save and start" }));

  expect(await screen.findByText("Goal page")).toBeTruthy();
  expect(created[0]).toMatchObject({ statement: "Fix the two save bugs users hit.", label: "bug", modality: "fix", title: "Fix the save bugs" });
});

test("a title the operator wrote with the pencil is kept over one written for them", async () => {
  const created: Record<string, unknown>[] = [];
  stub(created);
  show();
  await pick("describe");
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  await next(2);
  fireEvent.change(screen.getByTestId("description"), { target: { value: "Add CSV export" } });
  await next(3);
  await screen.findByRole("heading", { name: "Add a CSV export" });

  fireEvent.click(screen.getByRole("button", { name: "Change the goal's title" }));
  const field = screen.getByLabelText("The goal's title");
  fireEvent.change(field, { target: { value: "Exports" } });
  fireEvent.blur(field);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  expect(await screen.findByText("Goal page")).toBeTruthy();
  expect(created[0]).toMatchObject({ title: "Exports", start: false });
});

test("a panel's pencil opens it again, and another source starts the goal's makeup again", async () => {
  stub([]);
  show();
  await pick("describe");
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  await next(2);
  fireEvent.change(screen.getByTestId("description"), { target: { value: "Add CSV export" } });
  await next(3);
  await screen.findByText("How much it does without asking");

  fireEvent.click(screen.getByRole("button", { name: "Change: Where does the work come from?" }));
  expect(screen.getByTestId("step-1").getAttribute("data-open")).toBe("true");
  await pick("issues");
  // The description was the operator's, so it stays; the issues are asked for afresh.
  expect(screen.getByTestId("answer-1").textContent).toBe("Issues that already exist");
});

test("issues that exist cannot be chosen with no issue tracker set up, and say why", async () => {
  stub([], [], (url) => (url === "/v1/trackers" ? respond({ default: "mh", trackers: [{ ...BASE, scheme: "mh", title: "None", built_in: true }] }) : null));
  show();
  const card = await screen.findByTestId("source-issues");
  await waitFor(() => expect(card.hasAttribute("disabled")).toBe(true));
  expect(card.textContent).toContain("No issue tracker is set up yet");
});

test("a goal planned from an uploaded spec sends the file, with the title and description written from it", async () => {
  const created: Record<string, unknown>[] = [];
  stub(created, [], (url) =>
    url === "/v1/goals/draft-from-spec" ? respond({ title: "Data export", statement: "Users can export their data as CSV.", spec: "exports-prd.md" }) : null,
  );
  show();
  await pick("spec");
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  await next(2);

  fireEvent.click(screen.getByRole("radio", { name: "Upload a file" }));
  const file = new File(["# Exports\n\n- An export button.\n"], "exports-prd.md", { type: "text/markdown" });
  fireEvent.change(screen.getByTestId("spec-field").querySelector("input[type=file]")!, { target: { files: [file] } });
  await screen.findByTestId("spec-loaded");
  expect(await screen.findByRole("heading", { name: "Data export" })).toBeTruthy();
  await next(3);
  fireEvent.click(await screen.findByRole("button", { name: "Save and plan" }));

  expect(await screen.findByText("Goal page")).toBeTruthy();
  expect(created[0].spec).toEqual({ name: "exports-prd.md", text: "# Exports\n\n- An export button.\n" });
  expect(created[0]).toMatchObject({ title: "Data export", statement: "Users can export their data as CSV.", modality: "feature" });
});

// The form under a data router, so leaving it can be blocked and asked about.
function editorRouter(created: Record<string, unknown>[]) {
  stub(created);
  const router = createMemoryRouter(
    [
      { path: "/goals/new", element: <NewGoal /> },
      { path: "/goals", element: <p>Goals list</p> },
      { path: "/goals/:goalId", element: <p>Goal page</p> },
    ],
    { initialEntries: ["/goals/new"] },
  );
  render(
    <MantineProvider theme={theme}>
      <RouterProvider router={router} />
    </MantineProvider>,
  );
  return router;
}

test("stepping between panels is not leaving: nothing asks, under the router the console runs", async () => {
  // On production, Continue changed the address (?step=3) and the leave-page guard asked to save: the guard
  // runs only under a data router, which the other tests here do not use.
  const router = editorRouter([]);
  await pick("describe");
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  await next(2);

  expect(router.state.location.search).toContain("step=3");
  expect(screen.queryByText("Save your changes?")).toBeNull();
  expect(screen.getByTestId("step-3").getAttribute("data-open")).toBe("true");
  fireEvent.change(screen.getByTestId("description"), { target: { value: "Add CSV export" } });
  await next(3);
  expect(screen.queryByText("Save your changes?")).toBeNull();
  expect(await screen.findByText("How much it does without asking")).toBeTruthy();
});

test("leaving a started New goal asks first: keep editing stays, discard leaves", async () => {
  const router = editorRouter([]);
  await pick("describe");

  await act(() => router.navigate("/goals"));
  expect(await screen.findByText("Save your changes?")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
  await waitFor(() => expect(screen.queryByText("Save your changes?")).toBeNull());

  await act(() => router.navigate("/goals"));
  fireEvent.click(await screen.findByRole("button", { name: "Discard" }));
  expect(await screen.findByText("Goals list")).toBeTruthy();
});

test("an untouched New goal leaves without asking", async () => {
  const router = editorRouter([]);
  await screen.findByText("Where does the work come from?");
  await act(() => router.navigate("/goals"));
  expect(await screen.findByText("Goals list")).toBeTruthy();
});

test("backing out to the first question takes the written title with it", async () => {
  const router = editorRouter([]);
  await pick("describe");
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  await next(2);
  fireEvent.change(screen.getByTestId("description"), { target: { value: "Add CSV export" } });
  await next(3);
  await screen.findByRole("heading", { name: "Add a CSV export" });

  await act(() => router.navigate("/goals/new"));

  expect(await screen.findByRole("heading", { name: "New goal" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Add a CSV export" })).toBeNull();
});

test("the autonomy chosen last time is where a new goal starts, and this goal's choice is kept for the next", async () => {
  const created: Record<string, unknown>[] = [];
  const put: Record<string, unknown>[] = [];
  const mine = { planner: { repos: [], modality: "fix", latitude: null, max_usd: null, auto_merge: false, autonomy: "ask" }, appearance: "system", notifications: false };
  stub(created, [], (url, init) => {
    if (url !== "/v1/me/settings") return null;
    if (init?.method === "PUT") put.push(JSON.parse(String(init.body)));
    return respond(init?.method === "PUT" ? JSON.parse(String(init.body)) : mine);
  });
  show();
  await pick("describe");
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  await next(2);
  fireEvent.change(screen.getByTestId("description"), { target: { value: "Add CSV export" } });
  await next(3);

  const levels = await screen.findByRole("radiogroup", { name: "How much it does without asking" });
  expect((within(levels).getByRole("radio", { name: "Chill" }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(within(levels).getByRole("radio", { name: "Assertive" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  expect(await screen.findByText("Goal page")).toBeTruthy();
  expect(created[0]).toMatchObject({ autonomy: "full" });
  await waitFor(() => expect(put[0]).toMatchObject({ planner: { autonomy: "full", auto_merge: true } }));
});
