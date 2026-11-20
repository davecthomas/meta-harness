import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { Goal, IssueProgress } from "../../api";
import { theme } from "../../theme";
import { GoalBoard, columnOf } from "./GoalBoard";
import { HeldActions, StrictIssuesEdit, gapText, splitDrafts } from "./sizing";

afterEach(() => vi.unstubAllGlobals());

const LABEL = "meta-harness:goal-g-1";

function issue(n: number, fields: Partial<IssueProgress> = {}): IssueProgress {
  return {
    ref: `github:o/r#${n}`,
    repo: "o/r",
    state: "open",
    depends_on: [],
    refused_reason: null,
    task_id: `t-${n}`,
    task_status: "queued",
    task_status_reason: null,
    pr_url: null,
    pr_state: null,
    checks: null,
    ...fields,
  };
}

const LARGE = issue(1, {
  task_id: null,
  task_status: null,
  title: "Edit everything",
  size: "large",
  size_reasons: ["22 files"],
  split: ["Edit titles", "Edit labels"],
  paths: ["console/src/features/goals"],
  checks_named: ["npm test"],
  held_reason: "sized large: 22 files. Suggested split: Edit titles; Edit labels. Split it, or run it anyway",
});

function renderIn(element: React.ReactElement) {
  return render(
    <MantineProvider theme={theme}>
      <MemoryRouter>{element}</MemoryRouter>
    </MantineProvider>,
  );
}

function answering(body: unknown) {
  const asked: { url: string; method: string; body: any }[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    asked.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  return asked;
}

test("a held issue needs attention, and one with a gap runs where its task is", () => {
  const gap = issue(2, { size: "small", missing: ["paths"] });
  const byRef = new Map([LARGE, gap].map((i) => [i.ref, i]));
  expect(columnOf(LARGE, byRef)).toBe("attention");
  expect(columnOf(gap, byRef)).toBe("queued");
  expect(gapText(["acceptance_checks", "paths", "testable_outcome"])).toBe("Missing acceptance checks, a Paths: line and a testable outcome");
  expect(gapText([])).toBeNull();
});

test("the board shows a held issue's size and why it is held, and a gap on the card of one that runs", () => {
  const gap = issue(2, { size: "medium", missing: ["testable_outcome"] });
  renderIn(<GoalBoard issues={[LARGE, gap]} tasks={{}} spend={{}} actions={(i) => <HeldActions goalId="g-1" label={LABEL} issue={i} onChanged={() => {}} />} />);

  const attention = screen.getByTestId("column-attention");
  expect(within(attention).getByText("large")).toBeTruthy();
  // Said in parts, not as one sentence: what it is, why, and the split as a list.
  const held = within(attention).getByTestId("held-reason");
  expect(held.textContent).toContain("Not started: too large for one task");
  expect(within(held).getAllByRole("listitem").map((li) => li.textContent)).toEqual(expect.arrayContaining(["Edit titles", "Edit labels"]));
  expect(held.textContent).toContain("Split makes those separate issues, which run in its place.");
  expect(within(attention).getByRole("button", { name: "Run anyway" })).toBeTruthy();
  expect(within(attention).getByRole("button", { name: "Split" })).toBeTruthy();
  const queued = screen.getByTestId("column-queued");
  expect(within(queued).getByTestId("issue-gap").textContent).toBe("Missing a testable outcome");
  expect(within(queued).queryByRole("button", { name: "Run anyway" })).toBeNull();
});

test("Run anyway asks the planner to make the held issue a task", async () => {
  const asked = answering({ goal_id: "g-1", ref: LARGE.ref, run_anyway: true });
  const changed: number[] = [];
  renderIn(<HeldActions goalId="g-1" label={LABEL} issue={LARGE} onChanged={() => changed.push(1)} />);

  fireEvent.click(screen.getByRole("button", { name: "Run anyway" }));

  await waitFor(() => expect(changed).toEqual([1]));
  expect(asked[0].url).toContain("/v1/goals/g-1/issues/run-anyway");
  expect(asked[0]).toMatchObject({ method: "POST", body: { ref: LARGE.ref } });
});

const PART = (title: string, path: string) => ({
  title,
  body: `Do ${title}.\n\n## Outcome\n\n${title} show.\n\nPaths: ${path}\n\n## Acceptance checks\n\n- \`npm test\`\n\nSplit from #1\n`,
});

// The planner drafts the parts; the split creates them.
function splitting(drafted: { ok: boolean; drafts?: unknown[] }) {
  const asked: { url: string; body: any }[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    asked.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (url.endsWith("/split-drafts")) {
      return Promise.resolve(drafted.ok
        ? new Response(JSON.stringify({ ref: LARGE.ref, drafts: drafted.drafts }), { status: 200, headers: { "Content-Type": "application/json" } })
        : new Response(JSON.stringify({ detail: "the HTTP Gateway answered 503: busy. Try Split again" }), { status: 502, headers: { "Content-Type": "application/json" } }));
    }
    return Promise.resolve(new Response(JSON.stringify({ goal_id: "g-1", ref: LARGE.ref, created: ["github:o/r#10", "github:o/r#11"] }), { status: 201, headers: { "Content-Type": "application/json" } }));
  });
  return asked;
}

test("Split opens the planner's complete drafts of each part, refuses a part that is not complete, and creates them as edited", async () => {
  const asked = splitting({ ok: true, drafts: [PART("Edit titles", "console/src/features/goals"), PART("Edit labels", "console/src/features/labels")] });
  const changed: number[] = [];
  renderIn(<HeldActions goalId="g-1" label={LABEL} issue={LARGE} onChanged={() => changed.push(1)} />);

  fireEvent.click(screen.getByRole("button", { name: "Split" }));

  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(LABEL)).toBeTruthy();
  await waitFor(() => expect((within(dialog).getByLabelText("Body of issue 2") as HTMLTextAreaElement).value).toContain("## Outcome"));
  const body = within(dialog).getByLabelText("Body of issue 2") as HTMLTextAreaElement;
  expect(body.value).toContain("Paths: console/src/features/labels");
  expect(body.value).toContain("- `npm test`");

  // A part with no acceptance check cannot be created, and says what it lacks.
  fireEvent.change(body, { target: { value: "Labels only.\n\nPaths: console/src/features/labels" } });
  expect(within(dialog).getByText("Needs an acceptance check under ## Acceptance checks")).toBeTruthy();
  expect((within(dialog).getByRole("button", { name: "Create 2 issues" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(body, { target: { value: PART("Edit labels", "console/src/features/labels").body } });
  fireEvent.change(within(dialog).getByLabelText("Issue 1"), { target: { value: "Edit the titles" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Create 2 issues" }));

  await waitFor(() => expect(changed).toEqual([1]));
  const split = asked.find((a) => a.url.endsWith("/issues/split"))!;
  expect(split.body.ref).toBe(LARGE.ref);
  expect(split.body.issues.map((i: { title: string }) => i.title)).toEqual(["Edit the titles", "Edit labels"]);
});

test("when the planner cannot draft the parts, Split says so and starts from an outline to fill in", async () => {
  splitting({ ok: false });
  renderIn(<HeldActions goalId="g-1" label={LABEL} issue={LARGE} onChanged={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Split" }));
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByText(/The planner could not draft the parts: the HTTP Gateway answered 503/)).toBeTruthy();
  expect((within(dialog).getByLabelText("Issue 1") as HTMLInputElement).value).toBe("Edit titles");
  expect((within(dialog).getByLabelText("Body of issue 1") as HTMLTextAreaElement).value).toContain("## Outcome");
});

test("an issue split already offers only Run anyway, and a closed goal offers neither", () => {
  const split = { ...LARGE, held_reason: "split into #10, #11: close it once they cover it", split_into: ["github:o/r#10", "github:o/r#11"] };
  const { unmount } = renderIn(<HeldActions goalId="g-1" label={LABEL} issue={split} onChanged={() => {}} />);
  expect(screen.getByRole("button", { name: "Run anyway" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Split" })).toBeNull();
  unmount();
  // Recorded as split, whatever its reason says.
  const recorded = renderIn(<HeldActions goalId="g-1" label={LABEL} issue={{ ...LARGE, split_into: ["github:o/r#10"] }} onChanged={() => {}} />);
  expect(screen.queryByRole("button", { name: "Split" })).toBeNull();
  recorded.unmount();
  renderIn(<HeldActions goalId="g-1" label={LABEL} issue={LARGE} readOnly onChanged={() => {}} />);
  expect(screen.queryByRole("button", { name: "Run anyway" })).toBeNull();
});

test("an issue with no suggested split starts from one draft of itself", () => {
  const drafts = splitDrafts({ ...LARGE, split: [], paths: [], checks_named: [] });
  expect(drafts).toEqual([{ title: "Edit everything", body: "What to change.\n\n## Outcome\n\nWhat a reviewer sees when it is done.\n\nPaths: \n\n## Acceptance checks\n\n- " }]);
});

test("the strict switch saves strict_issues", async () => {
  const saved: unknown[] = [];
  const goal = { id: "g-1", status: "active", strict_issues: false } as Goal & { strict_issues: boolean };
  renderIn(<StrictIssuesEdit goal={goal} readOnly={false} save={async (f) => saved.push(f)} />);

  fireEvent.click(screen.getByRole("switch"));

  await waitFor(() => expect(saved).toEqual([{ strict_issues: true }]));
});
