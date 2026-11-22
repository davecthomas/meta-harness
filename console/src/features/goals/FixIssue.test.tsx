// Created by the meta-harness in run t-8dd648910614:run-1 on harness:pi-careful/claude:claude-opus-5-5.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { IssueProgress } from "../../api";
import { theme } from "../../theme";
import { GoalBoard } from "./GoalBoard";
import { IssuesPicker } from "./IssuesPicker";

afterEach(() => vi.unstubAllGlobals());

const REF = "github:o/r#4";
const MARKED: IssueProgress = {
  ref: REF,
  repo: "o/r",
  title: "Fix the crash on save",
  state: "open",
  depends_on: [],
  refused_reason: null,
  task_id: null,
  task_status: null,
  task_status_reason: null,
  pr_url: null,
  pr_state: null,
  checks: null,
  size: "medium",
  missing: ["acceptance_checks"],
  doubts: [{ part: "paths", reason: "it leaves out the tests" }],
  paths: ["src/save.py"],
  checks_named: [],
};

const DRAFTS = {
  ref: REF,
  drafts: [
    { part: "acceptance_checks", heading: "Acceptance checks", doubted: false, reason: "", text: "- `pytest tests/test_save.py`" },
    { part: "paths", heading: "More paths", doubted: true, reason: "it leaves out the tests", text: "Paths: tests/test_save.py" },
  ],
};

const ACCEPTED = {
  goal_id: "g-1",
  ref: REF,
  added: ["Acceptance checks", "More paths"],
  sizing: { size: "small", reasons: ["two files"], missing: [], doubts: [], split: [], paths: ["src/save.py", "tests/test_save.py"], checks: ["pytest tests/test_save.py"] },
  sizing_error: null,
};

type Asked = { url: string; method: string; body: any };

// fetch as the planner answers it: each route's answers in turn, the last one again after that.
function stub(routes: Record<string, { status: number; body: unknown }[]>): Asked[] {
  const asked: Asked[] = [];
  const seen: Record<string, number> = {};
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    asked.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
    const path = url.split("?")[0];
    const answers = routes[path];
    if (!answers) return Promise.resolve(new Response(JSON.stringify({ detail: "not stubbed" }), { status: 404 }));
    const n = seen[path] ?? 0;
    seen[path] = n + 1;
    const answer = answers[Math.min(n, answers.length - 1)];
    return Promise.resolve(new Response(JSON.stringify(answer.body), { status: answer.status, headers: { "Content-Type": "application/json" } }));
  });
  return asked;
}

const DRAFT_URL = "/v1/goals/g-1/issues/fix-draft";
const ACCEPT_URL = "/v1/goals/g-1/issues/fix-accept";
const accepts = (asked: Asked[]) => asked.filter((a) => a.url === ACCEPT_URL);

function board(onFixed: () => void = () => {}) {
  return render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <GoalBoard issues={[MARKED]} tasks={{}} spend={{}} goalId="g-1" onFixed={onFixed} />
      </MemoryRouter>
    </MantineProvider>,
  );
}

async function openDraft() {
  fireEvent.click(screen.getByRole("button", { name: "Fix with AI" }));
  return screen.findByTestId("fix-draft");
}

test("the draft shows beside the issue's text; accept sends the edited draft once and the card shows the new sizing", async () => {
  const asked = stub({ [DRAFT_URL]: [{ status: 200, body: DRAFTS }], [ACCEPT_URL]: [{ status: 200, body: ACCEPTED }] });
  const fixed: number[] = [];
  board(() => fixed.push(1));
  const card = screen.getByTestId("column-queued");
  expect(within(card).getByText("medium")).toBeTruthy();

  const draft = await openDraft();
  expect(asked.find((a) => a.url === DRAFT_URL)?.body).toEqual({ ref: REF });
  // Each part beside what the issue has now: no checks, and the paths it names.
  expect(within(draft).getByTestId("fix-now-acceptance_checks").textContent).toBe("Nothing");
  expect(within(draft).getByTestId("fix-now-paths").textContent).toBe("Paths: src/save.py");
  expect(within(draft).getByText("Its paths may not cover the change: it leaves out the tests")).toBeTruthy();
  const checks = within(draft).getByRole("textbox", { name: "Draft of Acceptance checks" }) as HTMLTextAreaElement;
  expect(checks.value).toBe("- `pytest tests/test_save.py`");
  fireEvent.change(checks, { target: { value: "- `pytest tests/test_save.py -k twice`" } });

  const accept = within(draft).getByRole("button", { name: "Accept" });
  fireEvent.click(accept);
  fireEvent.click(accept);
  await waitFor(() => expect(screen.queryByTestId("fix-draft")).toBeNull());

  expect(accepts(asked)).toHaveLength(1);
  expect(accepts(asked)[0].body).toEqual({
    ref: REF,
    parts: [
      { part: "acceptance_checks", text: "- `pytest tests/test_save.py -k twice`" },
      { part: "paths", text: "Paths: tests/test_save.py" },
    ],
  });
  expect(fixed).toEqual([1]);
  expect(within(card).getByText("small")).toBeTruthy();
  expect(within(card).queryByTestId("issue-gap")).toBeNull();
  expect(within(card).queryByTestId("issue-doubts")).toBeNull();
  expect(within(card).getByTestId("fix-done").textContent).toContain("Added Acceptance checks, More paths");
});

test("discard sends nothing and leaves the card as it was", async () => {
  const asked = stub({ [DRAFT_URL]: [{ status: 200, body: DRAFTS }], [ACCEPT_URL]: [{ status: 200, body: ACCEPTED }] });
  board();

  const draft = await openDraft();
  fireEvent.change(within(draft).getByRole("textbox", { name: "Draft of Paths" }), { target: { value: "Paths: docs" } });
  fireEvent.click(within(draft).getByRole("button", { name: "Discard" }));

  await waitFor(() => expect(screen.queryByTestId("fix-draft")).toBeNull());
  expect(accepts(asked)).toHaveLength(0);
  const card = screen.getByTestId("column-queued");
  expect(within(card).getByText("medium")).toBeTruthy();
  expect(within(card).getByTestId("issue-gap").textContent).toBe("Missing acceptance checks");
});

test("a 409 for a running task shows its message and keeps the draft, which accepts once the task ends", async () => {
  const running = "github:o/r#4's task t-9 is running, and its run has the issue's text already. The change can be accepted once the task ends";
  const asked = stub({
    [DRAFT_URL]: [{ status: 200, body: DRAFTS }],
    [ACCEPT_URL]: [{ status: 409, body: { detail: running } }, { status: 200, body: ACCEPTED }],
  });
  board();

  const draft = await openDraft();
  const paths = within(draft).getByRole("textbox", { name: "Draft of Paths" }) as HTMLTextAreaElement;
  fireEvent.change(paths, { target: { value: "Paths: tests/test_save.py, docs/save.md" } });
  fireEvent.click(within(draft).getByRole("button", { name: "Accept" }));

  const waiting = await within(draft).findByTestId("fix-waiting");
  expect(waiting.textContent).toContain(running);
  expect(screen.getByTestId("fix-draft")).toBeTruthy();
  expect((within(draft).getByRole("textbox", { name: "Draft of Paths" }) as HTMLTextAreaElement).value).toBe("Paths: tests/test_save.py, docs/save.md");
  expect(within(screen.getByTestId("column-queued")).getByText("medium")).toBeTruthy();

  // The task has ended: the same draft goes again.
  fireEvent.click(within(draft).getByRole("button", { name: "Accept" }));
  await waitFor(() => expect(screen.queryByTestId("fix-draft")).toBeNull());
  expect(accepts(asked)).toHaveLength(2);
  expect(accepts(asked)[1].body.parts[1]).toEqual({ part: "paths", text: "Paths: tests/test_save.py, docs/save.md" });
});

test("a failed draft says so on the card, and nothing changes", async () => {
  const asked = stub({ [DRAFT_URL]: [{ status: 502, body: { detail: "the model did not finish (length). Try Fix with AI again" } }] });
  board();

  fireEvent.click(screen.getByRole("button", { name: "Fix with AI" }));
  const failed = await screen.findByTestId("fix-failed");
  expect(failed.textContent).toContain("Fix with AI failed: the model did not finish (length)");
  expect(failed.textContent).toContain("Nothing was changed.");
  expect(screen.queryByTestId("fix-draft")).toBeNull();
  expect(accepts(asked)).toHaveLength(0);
  expect(within(screen.getByTestId("column-queued")).getByText("medium")).toBeTruthy();
});

test("a board with no goal named, or an issue with no marks, offers no Fix with AI", () => {
  stub({});
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <GoalBoard issues={[MARKED, { ...MARKED, ref: "github:o/r#5", missing: [], doubts: [] }]} tasks={{}} spend={{}} />
      </MemoryRouter>
    </MantineProvider>,
  );
  expect(screen.queryByRole("button", { name: "Fix with AI" })).toBeNull();
});

test("the issue picker marks the goal's issue and offers Fix with AI on it", async () => {
  const other = { ref: "github:o/r#5", number: 5, title: "Typo", labels: [], updated_at: "2026-11-09T08:00:00Z", held_by: null };
  const asked = stub({
    "/v1/goals/g-1/issues": [{ status: 200, body: { issues: [MARKED] } }],
    "/v1/repos/o/r/issues": [
      { status: 200, body: { issues: [{ ref: REF, number: 4, title: MARKED.title, labels: [], updated_at: "2026-11-09T10:00:00Z", held_by: "g-1" }, other], total: 2, next_cursor: null } },
    ],
    [DRAFT_URL]: [{ status: 200, body: DRAFTS }],
    [ACCEPT_URL]: [{ status: 200, body: ACCEPTED }],
  });
  render(
    <MantineProvider theme={theme}>
      <IssuesPicker opened onClose={() => undefined} repos={["o/r"]} goalId="g-1" action="Add to goal" onPick={() => undefined} />
    </MantineProvider>,
  );

  const marks = await screen.findByTestId(`picker-marks-${REF}`);
  expect(marks.textContent).toContain("Missing acceptance checks");
  expect(screen.getAllByRole("button", { name: "Fix with AI" })).toHaveLength(1);
  fireEvent.click(within(marks).getByRole("button", { name: "Fix with AI" }));
  const draft = await screen.findByTestId("fix-draft");
  fireEvent.click(within(draft).getByRole("button", { name: "Accept" }));
  await waitFor(() => expect(screen.queryByTestId("fix-draft")).toBeNull());
  expect(accepts(asked)).toHaveLength(1);
  expect(within(screen.getByTestId(`picker-marks-${REF}`)).queryByText("Missing acceptance checks")).toBeNull();
});
