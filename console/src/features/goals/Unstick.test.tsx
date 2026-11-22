import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { UnstickGoal } from "./Unstick";

afterEach(() => vi.unstubAllGlobals());

const PLAN = {
  moves: [
    { task_id: "t-1", name: "Split child parent", step: "merge", does: "merges its pull request" },
    { task_id: "t-2", name: "Planner goals report", step: "start_over", does: "starts it over on the current base" },
  ],
  left: [{ task_id: "t-3", name: "Deploy check", why: "its pull request needs something only a person gives on the code host" }],
};

function show(plan: unknown, did?: unknown) {
  const asked: { url: string; method: string }[] = [];
  const changed = vi.fn();
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    asked.push({ url, method });
    const body = method === "POST" ? did : plan;
    if (body === undefined) return Promise.resolve(new Response("{}", { status: 500 }));
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <UnstickGoal goalId="g-1" watch="t-1:ready" onChanged={changed} />
      </MemoryRouter>
    </MantineProvider>,
  );
  return { asked, changed };
}

test("one button names every stuck task's step before anything runs, and the ones that need a person", async () => {
  const { asked } = show(PLAN, { done: PLAN.moves, failed: [], left: PLAN.left });

  fireEvent.click(await screen.findByText("Get 2 tasks moving"));

  expect(asked.filter((a) => a.method === "POST")).toEqual([]);
  const steps = (await screen.findByTestId("unstick-steps")).textContent;
  expect(steps).toContain("Split child parent: merges its pull request");
  expect(steps).toContain("Planner goals report: starts it over on the current base");
  expect(screen.getByTestId("unstick-left").textContent).toContain("Deploy check: its pull request needs something only a person gives");
});

test("confirming takes the steps, says how many tasks moved, and links the ones left or refused", async () => {
  const refused = { ...PLAN.moves[1], why: "the Task API refused it: the task is running" };
  const { asked, changed } = show(PLAN, { done: [PLAN.moves[0]], failed: [refused], left: PLAN.left });

  fireEvent.click(await screen.findByText("Get 2 tasks moving"));
  await screen.findByTestId("unstick-steps");
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Get 2 tasks moving" }));

  const result = await screen.findByTestId("unstick-result");
  await waitFor(() => expect(asked.filter((a) => a.method === "POST").map((a) => a.url)).toEqual(["/v1/goals/g-1/unstick"]));
  expect(result.textContent).toContain("1 task is moving again.");
  expect(result.textContent).toContain("Planner goals report: the Task API refused it: the task is running");
  expect(result.textContent).toContain("Needs you: Deploy check");
  expect(screen.getByText("Deploy check").getAttribute("href")).toBe("/tasks/t-3");
  expect(changed).toHaveBeenCalled();
});

test("nothing shows when no task has a step to take, or when the steps cannot be read", async () => {
  const first = show({ moves: [], left: PLAN.left });
  await waitFor(() => expect(first.asked.length).toBe(1));
  expect(screen.queryByTestId("unstick")).toBeNull();
});
