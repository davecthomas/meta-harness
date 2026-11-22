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
    await screen.findByRole("checkbox", { name: "o/a", checked: true });
    fireEvent.change(screen.getByLabelText(/Description/), { target: { value: "Fix the save bugs" } });
    fireEvent.change(screen.getByRole("combobox", { name: /^Label/ }), { target: { value: "bug" } });
    fireEvent.click(screen.getByRole("button", { name: button }));
    expect(await screen.findByText("Goal page")).toBeTruthy();
  })();
}

test("a Jira goal starts on the default project, and its issues are read only for a real one", async () => {
  const asked: string[] = [];
  const jira = {
    scheme: "jira", title: "Jira", description: "", settings: [], credentials: [], missing_credentials: [], signed_in: true, configured: true,
    writes_progress: true, issue_url_prefix: null, goal_defaults: { project: "VL" },
    goal_settings: [{ name: "project", label: "Jira project", kind: "choice", required: true, help: "" }],
  };
  vi.stubGlobal("fetch", (url: string) => {
    if (url.startsWith("/v1/labels")) {
      asked.push(url);
      return respond({ repos: ["o/a"], labels: [{ label: "bug", total: 1, repos: { "o/a": 1 } }] });
    }
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/a" }] });
    if (url === "/v1/trackers") return respond({ default: "jira", trackers: [jira] });
    if (url === "/v1/trackers/jira/choices/project") return respond({ choices: [{ value: "VL", label: "Voltron (VL)" }, { value: "OPS", label: "Operations (OPS)" }] });
    return respond({ detail: "not stubbed" }, 404);
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/goals/new"]}>
        <Routes><Route path="/goals/new" element={<NewGoal />} /></Routes>
      </MemoryRouter>
    </MantineProvider>,
  );

  const project = await screen.findByDisplayValue("Voltron (VL)");
  fireEvent.change(project, { target: { value: "V" } });
  fireEvent.click(screen.getByRole("button", { name: "Draft a goal from issue labels" }));
  expect((await screen.findAllByText("bug")).length).toBeGreaterThan(0);

  expect(asked.length).toBeGreaterThan(0);
  for (const url of asked) expect(decodeURIComponent(url)).toContain('"project":"VL"');
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a goal with no tracker asks only for a description, and is saved to be planned", async () => {
  const created: Record<string, unknown>[] = [];
  const base = { description: "", settings: [], credentials: [], missing_credentials: [], signed_in: true, configured: true, writes_progress: true, issue_url_prefix: null, goal_settings: [], goal_defaults: {} };
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/a" }] });
    if (url === "/v1/trackers") return respond({ default: "github", trackers: [{ ...base, scheme: "github", title: "GitHub Issues" }, { ...base, scheme: "mh", title: "None: no issue tracking", built_in: true }] });
    if (url.startsWith("/v1/labels")) return respond({ repos: ["o/a"], labels: [] });
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
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  fireEvent.change(screen.getByLabelText(/Description/), { target: { value: "Add CSV export" } });
  // With a tracker, the goal says where its issues come from.
  expect(await screen.findByRole("radiogroup", { name: "Where its issues come from" })).toBeTruthy();

  fireEvent.change(screen.getByLabelText("Issue tracker"), { target: { value: "mh" } });

  expect((await screen.findByTestId("untracked-note")).textContent).toContain("nothing is written to any tracker");
  expect(screen.queryByRole("radiogroup", { name: "Where its issues come from" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: /^Label/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save and plan" }));
  expect(await screen.findByText("Goal page")).toBeTruthy();
  expect(created[0]).toMatchObject({ statement: "Add CSV export", repos: ["o/a"], tracker: "mh", modality: "feature", start: true });
  expect(created[0]).not.toHaveProperty("label");
});

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
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
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
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  fireEvent.click(screen.getByText("Plan them from the description"));
  expect(screen.getByTestId("where-note").textContent).toContain("splits the description into issues");
  fireEvent.click(screen.getByRole("button", { name: "Save and plan" }));

  await waitFor(() => expect(created).toHaveLength(1));
  expect(created[0]).toMatchObject({ statement: "Let users export their data", modality: "feature", start: true });
  expect(created[0].label).toBeUndefined();
});

test("a goal that cannot be saved says why beside the buttons, and where to fix it", async () => {
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/a" }] });
    if (url === "/v1/labels") return respond({ repos: ["o/a"], labels: [] });
    if (url === "/v1/goals" && init?.method === "POST") return respond({ detail: "the label mh:x is held by goal g-9" }, 422);
    return respond({ detail: "not stubbed" }, 404);
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/goals/new"]}>
        <Routes>
          <Route path="/goals/new" element={<NewGoal />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  fireEvent.change(screen.getByLabelText(/Description/), { target: { value: "Fix the save bugs" } });

  // Neither a label nor a picked issue: the form says which part of it to fill in.
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  const missing = await screen.findByTestId("goal-not-saved");
  expect(missing.textContent).toContain("give it a label, or pick issues by hand, under Where its issues come from");
  expect(within(screen.getByTestId("existing-issues")).getByText(/It needs at least one of the two/)).toBeTruthy();

  // A refusal by the service is said in the same place, with that nothing was created.
  fireEvent.change(screen.getByRole("combobox", { name: /^Label/ }), { target: { value: "mh:x" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(screen.getByTestId("goal-not-saved").textContent).toContain("the label mh:x is held by goal g-9"));
  expect(screen.getByTestId("goal-not-saved").textContent).toContain("Nothing was created");
});

function showNewGoal(created: Record<string, unknown>[]) {
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
}

test("a goal planned from a loaded specification needs no label, and sends the file's name and text", async () => {
  const created: Record<string, unknown>[] = [];
  showNewGoal(created);
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  fireEvent.change(screen.getByLabelText(/Description/), { target: { value: "Let users export their data" } });

  fireEvent.click(screen.getByRole("button", { name: "Plan a goal from a spec or an epic" }));
  // A file in the repo is offered first; one from this machine is the other way.
  fireEvent.click(screen.getByRole("radio", { name: "Upload a file" }));
  const file = new File(["# Exports\n\n- An export button.\n"], "exports-prd.md", { type: "text/markdown" });
  fireEvent.change(screen.getByTestId("spec-field").querySelector("input[type=file]")!, { target: { files: [file] } });

  expect((await screen.findByTestId("spec-loaded")).textContent).toContain("exports-prd.md, 31 characters.");
  // A goal with a specification is planned from it: the button says so, and no label is asked for.
  fireEvent.click(screen.getByRole("button", { name: "Save and plan" }));
  expect(await screen.findByText("Goal page")).toBeTruthy();
  expect(created[0].spec).toEqual({ name: "exports-prd.md", text: "# Exports\n\n- An export button.\n" });
  expect(created[0].label).toBeUndefined();
});

test("the settings most goals leave alone are folded away, and merging automatically sits above the buttons", async () => {
  showNewGoal([]);
  await screen.findByRole("checkbox", { name: "o/a" });

  const toggle = screen.getByTestId("more-settings");
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  // The budget is one of them, and the folded line says the one in force.
  expect(toggle.textContent).toContain("no budget, priority");
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByLabelText("Priority")).toBeTruthy();
  expect(screen.getByLabelText("Budget")).toBeTruthy();

  // The way to start a goal from a spec comes before the form's own fields, and the merge switch after them all.
  const order = (el: Element, other: Element) => !!(el.compareDocumentPosition(other) & Node.DOCUMENT_POSITION_FOLLOWING);
  const merge = screen.getByLabelText(/Merge pull requests automatically/);
  expect(order(screen.getByTestId("spec-field"), screen.getByLabelText(/Description/))).toBe(true);
  // Where the work is comes before both: the repos and the tracker are what they read.
  expect(order(screen.getByTestId("goal-where"), screen.getByTestId("spec-field"))).toBe(true);
  expect(within(screen.getByTestId("goal-where")).getByRole("checkbox", { name: "o/a" })).toBeTruthy();
  expect(order(toggle, merge)).toBe(true);
  expect(order(merge, screen.getByRole("button", { name: /^Save and/ }))).toBe(true);
});


function showWithRepos(names: string[]) {
  vi.stubGlobal("fetch", (url: string) => {
    if (url === "/v1/repos") return respond({ repos: names.map((name) => ({ name })) });
    if (url === "/v1/labels") return respond({ repos: names, labels: [] });
    return respond({ detail: "not stubbed" }, 404);
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/goals/new"]}>
        <Routes>
          <Route path="/goals/new" element={<NewGoal />} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("the only registered repository starts chosen", async () => {
  showWithRepos(["o/a"]);
  expect(await screen.findByRole("checkbox", { name: "o/a", checked: true })).toBeTruthy();
});

test("with several registered repositories none starts chosen", async () => {
  showWithRepos(["o/a", "o/b"]);
  expect(await screen.findByRole("checkbox", { name: "o/a", checked: false })).toBeTruthy();
  expect(screen.getByRole("checkbox", { name: "o/b", checked: false })).toBeTruthy();
});

// The New goal form over one repo, with a model that drafts a goal from a spec: `drafts` answers each call.
function showSpecGoal(created: Record<string, unknown>[], drafts: (() => Promise<Response>)[], asked: Record<string, unknown>[] = []) {
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/a" }] });
    if (url === "/v1/labels") return respond({ repos: ["o/a"], labels: [] });
    if (url === "/v1/goals/draft-from-spec") {
      asked.push(JSON.parse(String(init?.body)));
      return (drafts.length > 1 ? drafts.shift()! : drafts[0])();
    }
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
}

const drafted = () => respond({ title: "Data export", statement: "Users can export their data as CSV.", spec: "exports-prd.md" });

async function uploadSpec() {
  fireEvent.click(screen.getByRole("radio", { name: "Upload a file" }));
  const file = new File(["# Exports\n\n- An export button.\n"], "exports-prd.md", { type: "text/markdown" });
  fireEvent.change(screen.getByTestId("spec-field").querySelector("input[type=file]")!, { target: { files: [file] } });
  await screen.findByTestId("spec-loaded");
}

const where = (name: string) => screen.getByRole("radio", { name }) as HTMLInputElement;

test("a loaded spec chooses planning from the spec, and writes the title and description", async () => {
  const created: Record<string, unknown>[] = [];
  const asked: Record<string, unknown>[] = [];
  showSpecGoal(created, [drafted], asked);
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  expect(where("Issues that exist").checked).toBe(true);

  fireEvent.click(screen.getByRole("button", { name: "Plan a goal from a spec or an epic" }));
  await uploadSpec();

  expect(where("Plan them from the spec").checked).toBe(true);
  await screen.findByTestId("spec-draft-filled");
  expect((screen.getByLabelText(/^Title/) as HTMLInputElement).value).toBe("Data export");
  expect((screen.getByLabelText(/^Description/) as HTMLTextAreaElement).value).toBe("Users can export their data as CSV.");
  expect(asked[0]).toMatchObject({ spec: { name: "exports-prd.md" }, repos: ["o/a"] });
  // Where the split into tasks is decided, said before the button is pressed.
  expect(screen.getByTestId("where-note").textContent).toContain("A planning task reads exports-prd.md and the repos, and splits the work into issues");
  expect(screen.getByTestId("plan-note").textContent).toContain("Its split into tasks opens on the goal's page");

  fireEvent.click(screen.getByRole("button", { name: "Save and plan" }));
  expect(await screen.findByText("Goal page")).toBeTruthy();
  expect(created[0]).toMatchObject({ title: "Data export", statement: "Users can export their data as CSV.", modality: "feature", start: true });
});

test("a description the operator wrote is kept, with the spec's on offer", async () => {
  showSpecGoal([], [drafted]);
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  fireEvent.change(screen.getByLabelText(/^Description/), { target: { value: "My own words" } });
  fireEvent.click(screen.getByRole("button", { name: "Plan a goal from a spec or an epic" }));
  await uploadSpec();

  const kept = await screen.findByTestId("spec-draft-kept");
  expect((screen.getByLabelText(/^Description/) as HTMLTextAreaElement).value).toBe("My own words");
  expect((screen.getByLabelText(/^Title/) as HTMLInputElement).value).toBe("Data export");

  fireEvent.click(within(kept).getByRole("button", { name: /Use the one written from exports-prd.md/ }));
  expect((screen.getByLabelText(/^Description/) as HTMLTextAreaElement).value).toBe("Users can export their data as CSV.");
});

test("a draft that fails says why and tries again, and the goal can still be written by hand", async () => {
  showSpecGoal([], [() => respond({ detail: "could not draft the goal from exports-prd.md: busy. Try again, or write them yourself" }, 422), drafted]);
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  fireEvent.click(screen.getByRole("button", { name: "Plan a goal from a spec or an epic" }));
  await uploadSpec();

  const failed = await screen.findByTestId("spec-draft-failed");
  expect(failed.textContent).toContain("could not draft the goal from exports-prd.md: busy");
  fireEvent.click(within(failed).getByRole("button", { name: "Try again" }));
  await screen.findByTestId("spec-draft-filled");
  expect((screen.getByLabelText(/^Title/) as HTMLInputElement).value).toBe("Data export");
});

test("removing the spec takes back what it wrote and returns to issues that exist", async () => {
  showSpecGoal([], [drafted]);
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  fireEvent.click(screen.getByRole("button", { name: "Plan a goal from a spec or an epic" }));
  await uploadSpec();
  await screen.findByTestId("spec-draft-filled");

  fireEvent.click(screen.getByRole("button", { name: "Remove" }));

  await waitFor(() => expect((screen.getByLabelText(/^Description/) as HTMLTextAreaElement).value).toBe(""));
  expect((screen.getByLabelText(/^Title/) as HTMLInputElement).value).toBe("");
  expect(where("Issues that exist").checked).toBe(true);
});

test("planning from the spec with none chosen opens the spec's choices, and is not saved without one", async () => {
  const created: Record<string, unknown>[] = [];
  showSpecGoal(created, [drafted]);
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  fireEvent.change(screen.getByLabelText(/^Description/), { target: { value: "Let users export their data" } });

  fireEvent.click(screen.getByText("Plan them from the spec"));

  expect(screen.getByRole("radio", { name: "A file in the repo" })).toBeTruthy();
  expect(screen.getByTestId("where-note").textContent).toContain("Choose the spec above");
  fireEvent.click(screen.getByRole("button", { name: /^Save and/ }));
  expect((await screen.findByTestId("goal-not-saved")).textContent).toContain("Choose the spec");
  expect(created).toEqual([]);
});

test("another answer to where the issues come from lets go of the spec", async () => {
  showSpecGoal([], [drafted]);
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  fireEvent.click(screen.getByRole("button", { name: "Plan a goal from a spec or an epic" }));
  await uploadSpec();

  fireEvent.click(screen.getByText("Issues that exist"));

  expect(screen.queryByTestId("spec-loaded")).toBeNull();
  expect(where("Issues that exist").checked).toBe(true);
  expect(screen.getByTestId("existing-issues")).toBeTruthy();
});
