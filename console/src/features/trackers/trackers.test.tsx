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
    { name: "issue_type", label: "Issue type", kind: "choice", required: false, help: "Task when empty" },
    { name: "statuses", label: "Status names", kind: "statuses", required: false, help: "The projects' own names" },
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
    if (url === "/v1/trackers/jira/choices/issue_type") return respond({ choices: ["Bug", "Story", "Task"].map((name) => ({ value: name, label: name })) });
    if (url === "/v1/trackers/jira/choices/statuses") {
      if (statuses instanceof Error) return Promise.resolve(new Response(JSON.stringify({ detail: statuses.message }), { status: 422 }));
      return respond({ choices: statuses.map((name) => ({ value: name, label: name })) });
    }
    if (url === "/v1/trackers") return respond({ default: document.default, trackers: [GITHUB, JIRA] });
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/r" }] });
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
  expect(staged).toEqual([[["trackers", "jira"], { site: "" }]]);
});

test("a tracker that is set up shows its settings, its missing sign-in by name, and becomes the default", async () => {
  const document = { default: "github", trackers: { github: {}, jira: { site: "https://example.atlassian.net", statuses: { done: "Shipped" } } } };
  const staged = show(document, ["jira: site is the Jira site's address, such as https://example.atlassian.net"]);

  const jira = within(await screen.findByTestId("tracker-jira"));
  expect(jira.getByText("Sign-in missing")).toBeTruthy();
  expect(jira.getByText("MH_JIRA_TOKEN")).toBeTruthy();
  expect(jira.getByText(/add each as a secret of the deployment/)).toBeTruthy();
  expect(jira.getByText("https://example.atlassian.net")).toBeTruthy();
  expect(jira.getByText("Shipped")).toBeTruthy();
  expect(jira.getByText(/site is the Jira site's address/)).toBeTruthy();
  // The project is each goal's to say, not the site's configuration.
  expect(jira.getByText(/Each goal that uses Jira sets its own when it is created: Jira project/)).toBeTruthy();
  expect(jira.queryByText("Projects")).toBeNull();

  fireEvent.click(jira.getByRole("button", { name: "Make default" }));
  expect(staged).toEqual([[["default"], "jira"]]);
});

const SITE_STATUSES = ["Draft", "In Dev", "In Code Review", "Done"];
const WITH_JIRA = (statuses: Record<string, string>) => ({ default: "github", trackers: { github: {}, jira: { site: "https://example.atlassian.net", statuses } } });

test("a status name is picked from the site's own, in the site's spelling, and a name the site lacks is refused", async () => {
  const staged = show(WITH_JIRA({}), [], SITE_STATUSES);
  const jira = within(await screen.findByTestId("tracker-jira"));

  fireEvent.click(jira.getByRole("button", { name: "Edit Jira's status for in review" }));
  const input = await jira.findByRole("combobox", { name: "Jira's status for in review" });
  // The list is read when the page opens; a name typed before it arrives would not be checked.
  await waitFor(() => expect(input.getAttribute("placeholder")).toBe("Pick a status"));
  fireEvent.change(input, { target: { value: "In Code Reveiw" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(await jira.findByText(/Jira has no status named In Code Reveiw: pick one from the list/)).toBeTruthy();
  expect(staged).toEqual([]);

  fireEvent.change(input, { target: { value: "in code review" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(staged).toEqual([[["trackers", "jira", "statuses"], { in_review: "In Code Review" }]]));
});

test("an issue type is picked from the site's own too", async () => {
  const staged = show(WITH_JIRA({}), [], SITE_STATUSES);
  const jira = within(await screen.findByTestId("tracker-jira"));

  fireEvent.click(jira.getByRole("button", { name: "Edit Jira: Issue type" }));
  const input = await jira.findByRole("combobox", { name: "Jira: Issue type" });
  await waitFor(() => expect(input.getAttribute("placeholder")).toBe("Pick an issue type"));
  fireEvent.change(input, { target: { value: "Stroy" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(await jira.findByText(/Jira has no issue type named Stroy/)).toBeTruthy();

  fireEvent.change(input, { target: { value: "story" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(staged).toEqual([[["trackers", "jira", "issue_type"], "Story"]]));
});

test("a saved status name the site does not have is marked, and an unreadable list leaves typing open", async () => {
  show(WITH_JIRA({ done: "Shipped", todo: "draft" }), [], SITE_STATUSES);
  const jira = within(await screen.findByTestId("tracker-jira"));
  expect((await jira.findByTestId("status-unknown-done")).textContent).toBe("Jira has no status named Shipped");
  expect(jira.queryByTestId("status-unknown-todo")).toBeNull();
  cleanup();

  const staged = show(WITH_JIRA({}), [], new Error("Jira is not signed in: set MH_JIRA_TOKEN"));
  const unread = within(await screen.findByTestId("tracker-jira"));
  expect((await unread.findByText(/could not be listed, so type each exactly/)).textContent).toContain("set MH_JIRA_TOKEN");
  fireEvent.click(unread.getByRole("button", { name: "Edit Jira's status for done" }));
  const input = await unread.findByRole("combobox", { name: "Jira's status for done" });
  fireEvent.change(input, { target: { value: "Shipped" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(staged).toEqual([[["trackers", "jira", "statuses"], { done: "Shipped" }]]));
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
  const project = await screen.findByPlaceholderText("Choose one, or type its key");
  fireEvent.change(project, { target: { value: "vl" } });
  expect(changes.at(-1)).toEqual(["settings", { project: "VL" }]);

  rerender(<Form tracker="jira" settings={{ project: "VL" }} />);
  expect((await screen.findAllByText("Voltron (VL)")).length).toBeGreaterThan(0);
  rerender(<Form tracker="jira" settings={{ project: "XX" }} />);
  expect(await screen.findByText("No jira project has the key XX.")).toBeTruthy();
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
