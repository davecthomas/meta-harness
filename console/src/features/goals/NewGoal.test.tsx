import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, RouterProvider, Routes, createMemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { NewGoal } from "./NewGoal";
import { theme } from "../../theme";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

function fillAndPress(button: string, created: Record<string, unknown>[]) {
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/a" }] });
    if (url === "/v1/labels") return respond({ repos: ["o/a"], labels: [] });
    if (url === "/v1/goals" && init?.method === "POST") {
      created.push(JSON.parse(String(init.body)));
      return respond({ id: "g-1" }, 201);
    }
    return respond({ detail: "not stubbed" }, 404);
  });
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
  return (async () => {
    fireEvent.click(await screen.findByRole("checkbox", { name: "o/a" }));
    fireEvent.change(screen.getByLabelText(/Description/), { target: { value: "Fix the save bugs" } });
    fireEvent.change(screen.getByRole("combobox", { name: /^Label/ }), { target: { value: "bug" } });
    fireEvent.click(screen.getByRole("button", { name: button }));
    expect(await screen.findByText("Goal page")).toBeTruthy();
  })();
}

test("Save creates the goal without starting it and opens its page", async () => {
  const created: Record<string, unknown>[] = [];
  await fillAndPress("Save", created);
  await waitFor(() => expect(created).toHaveLength(1));
  expect(created[0]).toMatchObject({ statement: "Fix the save bugs", repos: ["o/a"], label: "bug", start: false });
});

test("Save and start creates the goal started", async () => {
  const created: Record<string, unknown>[] = [];
  await fillAndPress("Save and start", created);
  expect(created).toHaveLength(1);
  expect(created[0]).toMatchObject({ label: "bug", start: true });
});

function editorRouter(created: Record<string, unknown>[]) {
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/a" }] });
    if (url === "/v1/labels") return respond({ repos: ["o/a"], labels: [] });
    if (url === "/v1/goals" && init?.method === "POST") {
      created.push(JSON.parse(String(init.body)));
      return respond({ id: "g-1" }, 201);
    }
    return respond({ detail: "not stubbed" }, 404);
  });
  const router = createMemoryRouter(
    [
      { path: "/goals/new", element: <NewGoal /> },
      { path: "/goals", element: <p>Goals list</p> },
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

test("leaving a filled-in New goal asks first: keep editing stays, discard leaves", async () => {
  const router = editorRouter([]);
  fireEvent.change(await screen.findByLabelText(/Description/), { target: { value: "Fix the save bugs" } });

  await act(() => router.navigate("/goals"));
  expect(await screen.findByText("Save your changes?")).toBeTruthy();
  expect(screen.getByText(/the new goal "Fix the save bugs"/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
  await waitFor(() => expect(screen.queryByText("Save your changes?")).toBeNull());
  expect(screen.queryByText("Goals list")).toBeNull();

  await act(() => router.navigate("/goals"));
  fireEvent.click(await screen.findByRole("button", { name: "Discard" }));
  expect(await screen.findByText("Goals list")).toBeTruthy();
});

test("Save in the prompt saves the goal without starting it, then leaves", async () => {
  const created: Record<string, unknown>[] = [];
  const router = editorRouter(created);
  fireEvent.click(await screen.findByRole("checkbox", { name: "o/a" }));
  fireEvent.change(screen.getByLabelText(/Description/), { target: { value: "Fix the save bugs" } });
  fireEvent.change(screen.getByRole("combobox", { name: /^Label/ }), { target: { value: "bug" } });

  await act(() => router.navigate("/goals"));
  const prompt = await screen.findByRole("dialog", { name: "Save your changes?" });
  fireEvent.click(within(prompt).getByRole("button", { name: "Save" }));

  expect(await screen.findByText("Goals list")).toBeTruthy();
  expect(created[0]).toMatchObject({ statement: "Fix the save bugs", start: false });
});

test("an empty New goal leaves without asking", async () => {
  const router = editorRouter([]);
  await screen.findByLabelText(/Description/);
  await act(() => router.navigate("/goals"));
  expect(await screen.findByText("Goals list")).toBeTruthy();
});

test("a goal that plans its issues from the description needs no label and is created as a feature goal", async () => {
  const created: any[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/a" }] });
    if (url === "/v1/labels") return respond({ repos: ["o/a"], labels: [] });
    if (url === "/v1/goals" && init?.method === "POST") {
      created.push(JSON.parse(String(init.body)));
      return respond({ id: "g-9", status: "planning" }, 201);
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

  fireEvent.change(await screen.findByLabelText(/Description/), { target: { value: "Let users export their data" } });
  fireEvent.click(await screen.findByRole("checkbox", { name: "o/a" }));
  fireEvent.click(screen.getByText("Plan them from the description"));
  expect(screen.getByText(/A planning task reads the repos and drafts the issues/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Save and plan" }));

  await waitFor(() => expect(created).toHaveLength(1));
  expect(created[0]).toMatchObject({ statement: "Let users export their data", modality: "feature", start: true });
  expect(created[0].label).toBeUndefined();
});

test("a typed label selects its free issues, the picker locks them, and only issues picked by hand are sent as refs", async () => {
  const created: any[] = [];
  const ISSUES = [
    { ref: "github:o/a#1", number: 1, title: "Crash on save", labels: ["bug"], updated_at: "3", held_by: null },
    { ref: "github:o/a#2", number: 2, title: "Wrong total", labels: ["bug"], updated_at: "2", held_by: "g-9" },
    { ref: "github:o/a#3", number: 3, title: "Typo", labels: [], updated_at: "1", held_by: null },
  ];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/a" }] });
    if (url === "/v1/labels") return respond({ repos: ["o/a"], labels: [{ label: "bug", total: 2, repos: { "o/a": 2 }, held: 1, held_by: ["g-9"] }] });
    if (url === "/v1/repos/o/a/issues?label=bug") return respond({ issues: ISSUES.slice(0, 2) });
    if (url.startsWith("/v1/repos/o/a/issues")) return respond({ issues: ISSUES, total: 3, next_cursor: null });
    if (url === "/v1/goals" && init?.method === "POST") {
      created.push(JSON.parse(String(init.body)));
      return respond({ id: "g-1" }, 201);
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

  fireEvent.click(await screen.findByRole("checkbox", { name: "o/a" }));
  fireEvent.change(screen.getByLabelText(/Description/), { target: { value: "Fix the bugs" } });
  fireEvent.change(screen.getByRole("combobox", { name: /^Label/ }), { target: { value: "bug" } });

  const listed = await screen.findByTestId("label-issues");
  expect(listed.textContent).toContain("1 issue with this label, and 1 more that another goal holds");
  expect(listed.textContent).toContain("o/a#1");

  fireEvent.click(screen.getByRole("button", { name: "Pick issues" }));
  const locked = (await screen.findByRole("checkbox", { name: "github:o/a#1 is in the goal by its label" })) as HTMLInputElement;
  expect(locked.checked && locked.disabled).toBe(true);
  const held = screen.getByLabelText("Pick github:o/a#2") as HTMLInputElement;
  expect(held.checked).toBe(false);
  expect(screen.getByText("g-9")).toBeTruthy();
  fireEvent.click(screen.getByLabelText("Pick github:o/a#3"));
  fireEvent.click(screen.getByRole("button", { name: "Use these issues" }));
  expect(await screen.findByText(/1 picked by hand/)).toBeTruthy();
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Pick issues" })).toBeNull());

  // Clearing the label drops its issues from the selection; the one picked by hand stays.
  fireEvent.change(screen.getByRole("combobox", { name: /^Label/ }), { target: { value: "" } });
  await waitFor(() => expect(screen.queryByTestId("label-issues")).toBeNull());
  expect(screen.getByText(/1 picked by hand/)).toBeTruthy();
  fireEvent.change(screen.getByRole("combobox", { name: /^Label/ }), { target: { value: "bug" } });
  await screen.findByTestId("label-issues");

  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(created).toHaveLength(1));
  expect(created[0]).toMatchObject({ label: "bug", refs: ["github:o/a#3"] });
});
