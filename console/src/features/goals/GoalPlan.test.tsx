import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { Goal, GoalPlan as Plan, PlanDraft } from "../../api";
import { theme } from "../../theme";
import { GoalPlan, inOrder, splitBody } from "./GoalPlan";

afterEach(() => vi.unstubAllGlobals());

const GOAL = { id: "g-1", title: "Add exports", statement: "Let users export.", repos: ["o/a"], label: "meta-harness:goal-g-1", status: "planning" } as Goal;

function draft(draft_id: string, depends_on: string[] = [], body = `Build ${draft_id}.\n\n## Acceptance checks\n\n- make test`): PlanDraft {
  return { draft_id, repo: "o/a", title: `Build ${draft_id}`, body, labels: [], depends_on };
}

function planned(fields: Partial<NonNullable<Plan["plan"]>> = {}, top: Partial<Plan> = {}): Plan {
  return {
    goal_id: "g-1", status: "planning", planning_task_id: null, approved_at: null, label: "meta-harness:goal-g-1",
    plan: { version: 1, drafts: [draft("ui", ["api"]), draft("api", ["store"]), draft("store")], invalid: [], source: "planned", task_id: "t-plan", note: null, created: {}, created_by: "planner", created_at: "2026-11-10T10:00:00Z", ...fields },
    ...top,
  };
}

function show(first: Plan) {
  const asked: { url: string; method: string; body: any }[] = [];
  const approved: number[] = [];
  let now = first;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    asked.push({ url, method, body });
    if (method === "PUT") now = planned({ version: now.plan!.version + 1, drafts: body.drafts, source: "edited", created_by: "dave" });
    if (url.endsWith("/replan")) now = { ...now, planning_task_id: "t-plan-2" };
    if (url.endsWith("/approve")) now = { ...now, status: "active", approved_at: "2026-11-10T11:00:00Z" };
    return Promise.resolve(new Response(JSON.stringify(now), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <GoalPlan goal={GOAL} onApproved={() => approved.push(1)} />
      </MemoryRouter>
    </MantineProvider>,
  );
  return { asked, approved };
}

test("while the planning task drafts, the page says so and links to its run", async () => {
  show({ ...planned(), plan: null, planning_task_id: "t-plan" });
  expect(await screen.findByText("The planning task is drafting the plan")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Watch its run" }).getAttribute("href")).toBe("/tasks/t-plan");
  expect(screen.queryByRole("button", { name: "Approve the plan" })).toBeNull();
});

test("the plan shows each draft in the order the work can be done, with its checks set apart", async () => {
  show(planned());
  await screen.findByTestId("draft-store");
  const cards = screen.getAllByTestId(/^draft-/).map((c) => c.getAttribute("data-testid"));
  expect(cards).toEqual(["draft-store", "draft-api", "draft-ui"]);
  const api = screen.getByTestId("draft-api");
  expect(within(api).getByText("Acceptance checks")).toBeTruthy();
  expect(api.textContent).toContain("- make test");
  expect(api.textContent).toContain("Afterstore");
  expect(screen.getByTestId("plan-order").textContent).toContain("store");
  expect(screen.getByText(/Version 1, drafted by the planning task/)).toBeTruthy();
  expect(screen.getByRole("link", { name: "The planning task's run" }).getAttribute("href")).toBe("/tasks/t-plan");
});

test("an invalid plan lists its reasons and can't be approved", async () => {
  show(planned({ invalid: ["api: depends on ghost, which is no draft and no issue (owner/repo#number)"] }));
  expect(await screen.findByText("This plan can't be approved yet")).toBeTruthy();
  expect(screen.getByText(/depends on ghost/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Approve the plan" }) as HTMLButtonElement).disabled).toBe(true);
});

test("an edit is held until saved as the next version, and the plan can't be approved before that", async () => {
  const { asked } = show(planned());
  fireEvent.click(await screen.findByRole("button", { name: "Edit store" }));
  const dialog = await screen.findByRole("dialog", { name: "Edit store" });
  fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "Build the store, with indexes" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Keep the change" }));

  expect(await screen.findByText("Edits not saved yet")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Approve the plan" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Remove ui" }));
  fireEvent.click(screen.getByRole("button", { name: "Save as version 2" }));

  await waitFor(() => expect(asked.some((a) => a.method === "PUT")).toBe(true));
  const put = asked.find((a) => a.method === "PUT")!;
  expect(put.url).toBe("/v1/goals/g-1/plan");
  expect(put.body.drafts.map((d: PlanDraft) => [d.draft_id, d.title])).toEqual([["api", "Build api"], ["store", "Build the store, with indexes"]]);
  expect(await screen.findByText(/Version 2, edited by dave/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Approve the plan" }) as HTMLButtonElement).disabled).toBe(false);
});

test("Approve says what it will create, creates the issues and tells the page", async () => {
  const { asked, approved } = show(planned());
  fireEvent.click(await screen.findByRole("button", { name: "Approve the plan" }));
  const dialog = await screen.findByRole("dialog", { name: "Approve the plan" });
  expect(dialog.textContent).toContain("creates 3 issues on GitHub with the label meta-harness:goal-g-1");
  fireEvent.click(within(dialog).getByRole("button", { name: "Create the issues and start" }));
  await waitFor(() => expect(approved).toHaveLength(1));
  expect(asked.at(-1)).toMatchObject({ url: "/v1/goals/g-1/approve", method: "POST" });
});

test("Replan takes a note and starts a new planning task", async () => {
  const { asked } = show(planned());
  fireEvent.click(await screen.findByRole("button", { name: "Replan" }));
  const dialog = await screen.findByRole("dialog", { name: "Ask for a new plan" });
  fireEvent.change(within(dialog).getByLabelText("Note for the planner"), { target: { value: "Smaller issues." } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Replan" }));
  await waitFor(() => expect(asked.some((a) => a.url.endsWith("/replan"))).toBe(true));
  expect(asked.find((a) => a.url.endsWith("/replan"))!.body).toEqual({ note: "Smaller issues." });
  expect(await screen.findByText("The planning task is drafting the plan")).toBeTruthy();
});

test("drafts are ordered after what they wait on, and a body splits at its acceptance checks", () => {
  expect(inOrder([draft("ui", ["api"]), draft("api", ["store", "o/b#12"]), draft("docs"), draft("store")]).map((d) => d.draft_id)).toEqual(["store", "api", "ui", "docs"]);
  expect(inOrder([draft("a", ["b"]), draft("b", ["a"])]).map((d) => d.draft_id)).toEqual(["b", "a"]);
  expect(splitBody("Build it.\n\n## Acceptance checks\n\n- make test\n")).toEqual({ text: "Build it.", checks: "- make test" });
  expect(splitBody("No checks here.")).toEqual({ text: "No checks here.", checks: null });
});
