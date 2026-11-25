import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { TrackersView } from "./TrackersView";
import { issueUrl, refLabel, setTrackers, trackerOf, trackerTitle } from "./refs";
import { sourceQuery, type Tracker, type TrackerSetting } from "./api";
import { GoalTrackerFields } from "./GoalTrackerFields";
import { reasonTarget } from "../config/staging";
import { theme } from "../../theme";

// Issue trackers in the console: the configuration's view of every supported tracker, and refs of any tracker.

afterEach(() => {
  vi.unstubAllGlobals();
  setTrackers(null);
});

const base = { goal_settings: [] as TrackerSetting[], credentials: [] as string[], missing_credentials: [] as string[], signed_in: true, writes_progress: false, issue_url_prefix: null };
const GITHUB: Tracker = { ...base, scheme: "github", title: "GitHub Issues", description: "Each repo's own issues.", settings: [], configured: true };
const JIRA: Tracker = {
  ...base,
  scheme: "jira",
  title: "Jira",
  description: "Issues live in Jira Cloud projects.",
  settings: [
    { name: "site", label: "Site", kind: "text", required: true, help: "The Jira site's address" },
    {
      name: "projects", label: "Projects", kind: "entries", entry: "project", required: true, help: "The projects the meta-harness works in",
      settings: [
        { name: "issue_type", label: "Issue type", kind: "choice", required: false, help: "Task when empty" },
        { name: "statuses", label: "Status names", kind: "statuses", required: false, help: "The project's own names" },
      ],
    },
    { name: "default_project", label: "Default project", kind: "entry_of", of: "projects", required: true, help: "The project a new goal starts with" },
  ],
  goal_settings: [{ name: "project", label: "Jira project", kind: "choice", required: true, help: "The project the goal's issues are in." }],
  credentials: ["MH_JIRA_EMAIL", "MH_JIRA_TOKEN"],
  missing_credentials: ["MH_JIRA_TOKEN"],
  signed_in: false,
  configured: false,
  writes_progress: true,
  issue_url_prefix: "https://example.atlassian.net/browse/",
};

function respond(body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
}

function show(document: Record<string, any>, reasons: string[] = [], statuses: string[] | Error = []) {
  const staged: [string[], unknown][] = [];
  vi.stubGlobal("fetch", (url: string) => {
    if (url === "/v1/trackers/jira/choices/projects") return respond({ choices: [{ value: "OPS", label: "Operations (OPS)" }, { value: "VL", label: "Voltron (VL)" }] });
    if (url === "/v1/trackers/jira/choices/issue_type?project=VL") return respond({ choices: ["Bug", "Story", "Task"].map((name) => ({ value: name, label: name })) });
    if (url === "/v1/trackers/jira/choices/statuses?project=VL") {
      if (statuses instanceof Error) return Promise.resolve(new Response(JSON.stringify({ detail: statuses.message }), { status: 422 }));
      return respond({ choices: statuses.map((name) => ({ value: name, label: name })) });
    }
    if (url === "/v1/trackers") return respond({ default: document.default, trackers: [GITHUB, JIRA] });
    if (url === "/v1/repos" || url === "/v1/repos?pushable=1") return respond({ repos: [{ name: "o/r" }] });
    return respond({});
  });
  render(
    <MantineProvider theme={theme}>
      <TrackersView
        staging={{
          document,
          original: document,
          stage: (path, value) => staged.push([path as string[], value]),
          reasons: (target) => reasons.filter((r) => reasonTarget("trackers", document, r) === target),
        }}
      />
    </MantineProvider>,
  );
  return staged;
}

test("a goal's project is typed only when the list cannot be read, and counts once the field is left", async () => {
  vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify({ detail: "Jira did not answer" }), { status: 502 })));
  const changes: unknown[] = [];
  render(
    <MantineProvider theme={theme}>
      <GoalTrackerFields trackers={[{ ...JIRA, configured: true }]} tracker="jira" onTracker={() => undefined} settings={{}} onSettings={(next) => changes.push(next)} />
    </MantineProvider>,
  );
  expect(await screen.findByText(/The list could not be read, so type the key exactly: Jira did not answer/)).toBeTruthy();
  const key = screen.getByLabelText(/Jira project/);
  fireEvent.change(key, { target: { value: "v" } });
  fireEvent.change(key, { target: { value: "vl" } });
  expect(changes).toEqual([]);
  fireEvent.blur(key);
  expect(changes).toEqual([{ project: "VL" }]);
});

test("every supported tracker is listed, and one that is not set up is set up with its required settings empty", async () => {
  const staged = show({ default: "github", trackers: { github: {} } });

  const github = within(await screen.findByTestId("tracker-github"));
  expect(github.getByText("Default for new goals")).toBeTruthy();
  expect(github.getByText("Set up")).toBeTruthy();
  expect(github.queryByRole("button", { name: "Remove" })).toBeNull();
  const jira = within(screen.getByTestId("tracker-jira"));
  expect(jira.getByText("Not set up")).toBeTruthy();
  expect(jira.queryByText("Site")).toBeNull();

  fireEvent.click(jira.getByRole("button", { name: "Set up Jira" }));
  expect(staged).toEqual([[["trackers", "jira"], { site: "", projects: {}, default_project: "" }]]);
});

test("a tracker that is set up shows its settings, its missing sign-in by name, and becomes the default", async () => {
  const document = { default: "github", trackers: { github: {}, jira: { site: "https://example.atlassian.net", default_project: "VL", projects: { VL: { statuses: { done: "Shipped" } } } } } };
  const staged = show(document, ["jira: site is the Jira site's address, such as https://example.atlassian.net"]);

  const jira = within(await screen.findByTestId("tracker-jira"));
  expect(jira.getByText("Sign-in missing")).toBeTruthy();
  expect(jira.getByText("MH_JIRA_TOKEN")).toBeTruthy();
  expect(jira.getByText(/add each as a secret of the deployment/)).toBeTruthy();
  expect(jira.getByText("https://example.atlassian.net")).toBeTruthy();
  expect(jira.getByText("Shipped")).toBeTruthy();
  expect(jira.getByText(/site is the Jira site's address/)).toBeTruthy();
  expect(jira.getByText(/Each goal that uses Jira sets its own when it is created: Jira project/)).toBeTruthy();
  // The projects the meta-harness works in are the site's setup; which of them a goal uses is the goal's.
  expect(jira.getByText("Projects")).toBeTruthy();
  expect(within(jira.getByTestId("entry-VL")).getByText("Default")).toBeTruthy();

  fireEvent.click(jira.getByRole("button", { name: "Make default" }));
  expect(staged).toEqual([[["default"], "jira"]]);
});

test("a field's help is never at the smallest size and dimmed at once", async () => {
  show(WITH_JIRA({}));
  const jira = within(await screen.findByTestId("tracker-jira"));
  const helps = ["The Jira site's address", "The projects the meta-harness works in", "The project a new goal starts with", "Task when empty", "The project's own names"];
  for (const help of helps) {
    const text = jira.getByText(help);
    const size = text.getAttribute("data-size");
    expect(size, help).toBeTruthy();
    const dimmed = text.style.color === "var(--mantine-color-dimmed)";
    expect(dimmed && size === "xs", help).toBe(false);
  }
});

const SITE_STATUSES = ["Draft", "In Dev", "In Code Review", "Done"];
const WITH_JIRA = (vl: Record<string, unknown>, more: Record<string, unknown> = {}) => ({
  default: "github",
  trackers: { github: {}, jira: { site: "https://example.atlassian.net", default_project: "VL", projects: { VL: vl }, ...more } },
});

test("a project's status name is picked from that project's own, in its spelling, and one it lacks is refused", async () => {
  const staged = show(WITH_JIRA({}), [], SITE_STATUSES);
  const vl = within(await screen.findByTestId("entry-VL"));
  expect(await vl.findByText("Voltron (VL)")).toBeTruthy();
  expect(vl.getByText("Default")).toBeTruthy();

  fireEvent.click(vl.getByRole("button", { name: "Edit VL's status for in review" }));
  const input = await vl.findByRole("combobox", { name: "VL's status for in review" });
  // The list is read when the page opens; a name typed before it arrives would not be checked.
  await waitFor(() => expect(input.getAttribute("placeholder")).toBe("Pick a status"));
  fireEvent.change(input, { target: { value: "In Code Reveiw" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(await vl.findByText(/VL has no status named In Code Reveiw: pick one from the list/)).toBeTruthy();
  expect(staged).toEqual([]);

  fireEvent.change(input, { target: { value: "in code review" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(staged).toEqual([[["trackers", "jira", "projects", "VL", "statuses"], { in_review: "In Code Review" }]]));
});

test("a project's issue type is picked from that project's own too", async () => {
  const staged = show(WITH_JIRA({}), [], SITE_STATUSES);
  const vl = within(await screen.findByTestId("entry-VL"));

  fireEvent.click(vl.getByRole("button", { name: "Edit VL: Issue type" }));
  const input = await vl.findByRole("combobox", { name: "VL: Issue type" });
  await waitFor(() => expect(input.getAttribute("placeholder")).toBe("Pick an issue type"));
  fireEvent.change(input, { target: { value: "Stroy" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(await vl.findByText(/VL has no issue type named Stroy/)).toBeTruthy();

  fireEvent.change(input, { target: { value: "story" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(staged).toEqual([[["trackers", "jira", "projects", "VL", "issue_type"], "Story"]]));
});

test("a saved status name the project does not have is marked, and an unreadable list leaves typing open", async () => {
  show(WITH_JIRA({ statuses: { done: "Shipped", todo: "draft" } }), [], SITE_STATUSES);
  const vl = within(await screen.findByTestId("entry-VL"));
  expect((await vl.findByTestId("status-unknown-done")).textContent).toBe("VL has no status named Shipped");
  expect(vl.queryByTestId("status-unknown-todo")).toBeNull();
  cleanup();

  const staged = show(WITH_JIRA({}), [], new Error("Jira is not signed in: set MH_JIRA_TOKEN"));
  const unread = within(await screen.findByTestId("entry-VL"));
  expect((await unread.findByText(/status names could not be listed, so type each exactly/)).textContent).toContain("set MH_JIRA_TOKEN");
  fireEvent.click(unread.getByRole("button", { name: "Edit VL's status for done" }));
  const input = await unread.findByRole("combobox", { name: "VL's status for done" });
  fireEvent.change(input, { target: { value: "Shipped" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(staged).toEqual([[["trackers", "jira", "projects", "VL", "statuses"], { done: "Shipped" }]]));
});

test("a project is added by picking it from the site's, the first becomes the default, and the default is not removed", async () => {
  const none = { default: "github", trackers: { github: {}, jira: { site: "https://example.atlassian.net", projects: {}, default_project: "" } } };
  const staged = show(none);
  const jira = within(await screen.findByTestId("tracker-jira"));
  expect(jira.getByText("None yet. Add the first below.")).toBeTruthy();
  expect(jira.getByText("Add one above first")).toBeTruthy();

  const input = jira.getByRole("combobox", { name: "Add a project" });
  await waitFor(() => expect(input.getAttribute("placeholder")).toBe("Pick a project to add"));
  fireEvent.change(input, { target: { value: "ZZ" } });
  fireEvent.click(jira.getByRole("button", { name: "Add project" }));
  expect(await jira.findByText(/Jira has no project ZZ that its sign-in can see/)).toBeTruthy();
  expect(staged).toEqual([]);

  fireEvent.change(input, { target: { value: "Voltron (VL)" } });
  fireEvent.click(jira.getByRole("button", { name: "Add project" }));
  expect(staged).toEqual([[["trackers", "jira", "projects", "VL"], {}], [["trackers", "jira", "default_project"], "VL"]]);
  cleanup();

  const two = WITH_JIRA({}, { projects: { VL: {}, OPS: {} } });
  const removed = show(two);
  const listed = within(await screen.findByTestId("tracker-jira"));
  expect((listed.getByRole("button", { name: "Remove VL" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(listed.getByRole("button", { name: "Remove OPS" }));
  expect(removed).toEqual([[["trackers", "jira", "projects", "OPS"], undefined]]);
});

test("a setup from before projects were listed hands its issue type and status names to the first project added", async () => {
  const before = { default: "github", trackers: { github: {}, jira: { site: "https://example.atlassian.net", issue_type: "Story", statuses: { done: "Done" } } } };
  const staged = show(before);
  const jira = within(await screen.findByTestId("tracker-jira"));
  expect((await jira.findByTestId("entries-before-projects")).textContent).toContain("its issue type and status names apply to every project");

  const input = jira.getByRole("combobox", { name: "Add a project" });
  await waitFor(() => expect(input.getAttribute("placeholder")).toBe("Pick a project to add"));
  fireEvent.change(input, { target: { value: "vl" } });
  fireEvent.keyDown(input, { key: "Enter" });

  expect(staged).toEqual([
    [["trackers", "jira", "projects", "VL"], { issue_type: "Story", statuses: { done: "Done" } }],
    [["trackers", "jira", "issue_type"], undefined],
    [["trackers", "jira", "statuses"], undefined],
    [["trackers", "jira", "default_project"], "VL"],
  ]);
});

test("the New goal form asks what the chosen tracker asks of a goal, and lists the choices by name", async () => {
  vi.stubGlobal("fetch", (url: string) => {
    if (url === "/v1/trackers/jira/choices/project") {
      return respond({ choices: [{ value: "OPS", label: "Operations (OPS)" }, { value: "VL", label: "Voltron (VL)" }] });
    }
    return respond({});
  });
  const changes: unknown[] = [];
  function Form({ tracker, settings }: { tracker: string; settings: Record<string, string> }) {
    return (
      <MantineProvider theme={theme}>
        <GoalTrackerFields
          trackers={[GITHUB, { ...JIRA, configured: true }]}
          tracker={tracker}
          onTracker={(scheme) => changes.push(["tracker", scheme])}
          settings={settings}
          onSettings={(next) => changes.push(["settings", next])}
        />
      </MantineProvider>
    );
  }
  const { rerender } = render(<Form tracker="github" settings={{}} />);
  expect(screen.queryByPlaceholderText("Choose one, or type its key")).toBeNull();
  fireEvent.change(screen.getByLabelText("Issue tracker"), { target: { value: "jira" } });
  expect(changes).toEqual([["tracker", "jira"]]);

  rerender(<Form tracker="jira" settings={{}} />);
  // A pick, so nothing half-typed becomes the goal's project.
  const project = await screen.findByPlaceholderText("Choose one");
  fireEvent.change(project, { target: { value: "V" } });
  expect(changes).toEqual([["tracker", "jira"]]);
  fireEvent.click(await screen.findByText("Voltron (VL)"));
  expect(changes.at(-1)).toEqual(["settings", { project: "VL" }]);

  rerender(<Form tracker="jira" settings={{ project: "VL" }} />);
  expect(await screen.findByDisplayValue("Voltron (VL)")).toBeTruthy();
  rerender(<Form tracker="jira" settings={{ project: "XX" }} />);
  expect(await screen.findByText("XX is not one of these: choose one that is.")).toBeTruthy();
});

test("the New goal form lists the people for the assignee setting, from the goal's project or repos", async () => {
  const assignee: TrackerSetting = { name: "assignee", label: "Assignee", kind: "choice", required: false, help: "Who the goal's issues are assigned to." };
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    if (url === "/v1/trackers/jira/choices/project") return respond({ choices: [{ value: "VL", label: "Voltron (VL)" }] });
    if (url === "/v1/trackers/jira/choices/assignee?project=VL&repos=o%2Fr") {
      return respond({ choices: [{ value: "acc-alice", label: "Alice Ng (alice@example.com)" }, { value: "acc-bob", label: "Bob Ray" }] });
    }
    if (url === "/v1/trackers/github/choices/assignee?repos=o%2Fr%2Co%2Fs") return respond({ choices: [{ value: "octocat", label: "octocat" }] });
    return respond({});
  });
  const jira = { ...JIRA, configured: true, signed_in: true, goal_settings: [...JIRA.goal_settings, assignee] };
  const github = { ...GITHUB, goal_settings: [assignee] };
  const changes: Record<string, string>[] = [];
  const fields = (tracker: string, settings: Record<string, string>, repos: string[]) => (
    <MantineProvider theme={theme}>
      <GoalTrackerFields trackers={[github, jira]} tracker={tracker} onTracker={() => undefined} settings={settings} onSettings={(next) => changes.push(next)} repos={repos} />
    </MantineProvider>
  );
  // The field's input: its label also names the dropdown's list.
  const field = async () => (await screen.findAllByLabelText("Assignee")).find((el) => el.tagName === "INPUT")!;

  // Jira's people are a project's: none are asked for until the goal has one.
  const { rerender } = render(fields("jira", {}, ["o/r"]));
  expect((await field()).getAttribute("placeholder")).toBe("Choose the project first");
  expect(asked.filter((url) => url.includes("/assignee"))).toEqual([]);

  rerender(fields("jira", { project: "VL" }, ["o/r"]));
  const person = await field();
  await waitFor(() => expect(person.getAttribute("placeholder")).toBe("Choose one, or leave empty"));
  fireEvent.click(person);
  expect(await screen.findByText("Bob Ray")).toBeTruthy();
  fireEvent.click(await screen.findByText("Alice Ng (alice@example.com)"));
  expect(changes.at(-1)).toEqual({ project: "VL", assignee: "acc-alice" });

  // Not required: picking the one chosen again empties it, and the setting is left out.
  rerender(fields("jira", { project: "VL", assignee: "acc-alice" }, ["o/r"]));
  expect(await screen.findByDisplayValue("Alice Ng (alice@example.com)")).toBeTruthy();
  fireEvent.click(await field());
  fireEvent.click(await screen.findByRole("option", { name: "Alice Ng (alice@example.com)", hidden: true }));
  expect(changes.at(-1)).toEqual({ project: "VL" });

  // GitHub's people are those every one of the goal's repos can assign.
  rerender(fields("github", {}, ["o/r", "o/s"]));
  fireEvent.click(await field());
  fireEvent.click(await screen.findByText("octocat"));
  expect(changes.at(-1)).toEqual({ assignee: "octocat" });
});

test("the New goal form lists the parents of the chosen project, and none before a project is chosen", async () => {
  const parent: TrackerSetting = { name: "parent", label: "Parent", kind: "choice", required: false, help: "The epic the goal's plan's issues are created under." };
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    if (url === "/v1/trackers/jira/choices/project") return respond({ choices: [{ value: "OPS", label: "Operations (OPS)" }, { value: "VL", label: "Voltron (VL)" }] });
    if (url === "/v1/trackers/jira/choices/parent?project=VL") return respond({ choices: [{ value: "jira:VL-100", label: "VL-100: Checkout" }] });
    if (url === "/v1/trackers/jira/choices/parent?project=OPS") return respond({ choices: [{ value: "jira:OPS-7", label: "OPS-7: Uptime" }] });
    return respond({});
  });
  const jira = { ...JIRA, configured: true, signed_in: true, goal_settings: [...JIRA.goal_settings, parent] };
  const changes: Record<string, string>[] = [];
  const fields = (settings: Record<string, string>) => (
    <MantineProvider theme={theme}>
      <GoalTrackerFields trackers={[jira]} tracker="jira" onTracker={() => undefined} settings={settings} onSettings={(next) => changes.push(next)} />
    </MantineProvider>
  );
  const field = async () => (await screen.findAllByLabelText("Parent")).find((el) => el.tagName === "INPUT")!;

  const { rerender } = render(fields({}));
  expect((await field()).getAttribute("placeholder")).toBe("Choose the project first");
  expect(asked.filter((url) => url.includes("/parent"))).toEqual([]);

  rerender(fields({ project: "VL" }));
  const epic = await field();
  await waitFor(() => expect(epic.getAttribute("placeholder")).toBe("Choose one, or leave empty"));
  fireEvent.click(epic);
  fireEvent.click(await screen.findByText("VL-100: Checkout"));
  expect(changes.at(-1)).toEqual({ project: "VL", parent: "jira:VL-100" });
  expect(screen.queryByText("OPS-7: Uptime")).toBeNull();

  // Another project's parents are its own.
  rerender(fields({ project: "OPS" }));
  fireEvent.click(await field());
  expect(await screen.findByText("OPS-7: Uptime")).toBeTruthy();
  expect(asked).toContain("/v1/trackers/jira/choices/parent?project=OPS");
});

test("with one tracker set up the form still says which, and one with no sign-in says no issues will be read", () => {
  const fields = (trackers: Tracker[], tracker: string) => (
    <MantineProvider theme={theme}>
      <GoalTrackerFields trackers={trackers} tracker={tracker} onTracker={() => undefined} settings={{}} onSettings={() => undefined} />
    </MantineProvider>
  );
  const { rerender } = render(fields([GITHUB], "github"));
  expect(screen.getByTestId("one-tracker").textContent).toContain("GitHub Issues");
  expect(screen.getByTestId("one-tracker").textContent).toContain("The only one set up");
  expect(screen.queryByTestId("tracker-signed-out")).toBeNull();

  rerender(fields([{ ...GITHUB, signed_in: false }], "github"));
  expect(screen.getByTestId("tracker-signed-out").textContent).toContain("reads no issues until an admin adds one");

  rerender(fields([], ""));
  expect(screen.getByTestId("one-tracker").textContent).toContain("None is set up");
});

test("the label and issue lists are asked for a goal's tracker and what it told it", () => {
  expect(sourceQuery(undefined)).toEqual({ tracker: undefined, settings: undefined });
  expect(sourceQuery({ tracker: "github" })).toEqual({ tracker: "github", settings: undefined });
  expect(sourceQuery({ tracker: "jira", settings: { project: "VL", other: "" } })).toEqual({ tracker: "jira", settings: '{"project":"VL"}' });
});

test("a ref reads without its tracker and links to its issue, whichever tracker it names", () => {
  expect(refLabel("github:o/r#12")).toBe("o/r#12");
  expect(refLabel("jira:VL-12")).toBe("VL-12");
  expect(trackerOf("jira:VL-12")).toBe("jira");
  expect(issueUrl("github:o/r#12")).toBe("https://github.com/o/r/issues/12");
  expect(issueUrl("jira:VL-12")).toBeNull();
  expect(trackerTitle("jira")).toBe("jira");

  setTrackers({ default: "jira", trackers: [GITHUB, JIRA] });
  expect(issueUrl("jira:VL-12")).toBe("https://example.atlassian.net/browse/VL-12");
  expect(trackerTitle("jira")).toBe("Jira");
  expect(issueUrl("smoke:1")).toBeNull();
});

test("a refused save's reason goes to the tracker it is about, or to the default", () => {
  const document = { default: "jira", trackers: { github: {}, jira: {} } };
  expect(reasonTarget("trackers", document, "jira: site is the Jira site's address")).toBe("tracker:jira");
  expect(reasonTarget("trackers", document, "default must be one of the trackers set up (github), not 'jira'")).toBe("default");
  expect(reasonTarget("trackers", document, "linear: not a supported tracker")).toBeNull();
});

test("a goal's switch setting is on as yes, and off leaves it out", () => {
  const assigned: TrackerSetting = { name: "assigned", label: "Take issues assigned to the meta-harness", kind: "switch", required: false, help: "Assigning an issue there hands it over." };
  const jira = { ...JIRA, configured: true, signed_in: true, goal_settings: [assigned] };
  const changes: Record<string, string>[] = [];
  const fields = (settings: Record<string, string>) => (
    <MantineProvider theme={theme}>
      <GoalTrackerFields trackers={[jira]} tracker="jira" onTracker={() => undefined} settings={settings} onSettings={(next) => changes.push(next)} />
    </MantineProvider>
  );
  const { rerender } = render(fields({ project: "VL" }));
  fireEvent.click(screen.getByRole("switch", { name: /Take issues assigned to the meta-harness/ }));
  expect(changes).toEqual([{ project: "VL", assigned: "yes" }]);

  rerender(fields({ project: "VL", assigned: "yes" }));
  fireEvent.click(screen.getByRole("switch", { name: /Take issues assigned to the meta-harness/ }));
  expect(changes[1]).toEqual({ project: "VL" });
});

test("the choice of no tracker can be the default, and has nothing to set up or remove", async () => {
  const none: Tracker = { ...base, scheme: "mh", title: "None: no issue tracking", description: "Tasks are kept in the meta-harness.", settings: [], configured: true, built_in: true };
  const staged: [string[], unknown][] = [];
  vi.stubGlobal("fetch", (url: string) => (url === "/v1/trackers" ? respond({ default: "github", trackers: [GITHUB, none] }) : respond({})));
  const document = { default: "github", trackers: { github: {} } };
  render(
    <MantineProvider theme={theme}>
      <TrackersView staging={{ document, original: document, stage: (path, value) => staged.push([path as string[], value]), reasons: () => [] }} />
    </MantineProvider>,
  );

  const card = within(await screen.findByTestId("tracker-mh"));
  expect(card.queryByText("Set up")).toBeNull();
  expect(card.queryByRole("button", { name: "Remove" })).toBeNull();
  expect(card.queryByRole("button", { name: /Set up/ })).toBeNull();
  fireEvent.click(card.getByRole("button", { name: "Make default" }));
  expect(staged).toEqual([[["default"], "mh"]]);
});

test("a task kept in the meta-harness reads as that, with no link to anywhere else", () => {
  expect(refLabel("mh:i-0123456789ab")).toBe("Kept here · 6789ab");
  expect(issueUrl("mh:i-0123456789ab")).toBeNull();
  expect(refLabel("jira:VL-12")).toBe("VL-12");
});
