// Created by the meta-harness in run t-46c9a7c1ad78:run-1 on harness:pi-careful/claude:claude-opus-5-5.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { NewGoal } from "./NewGoal";
import { ImproveGoal } from "./ImproveGoal";
import { api } from "../../api";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

const PROPOSAL = {
  goal_id: null,
  statement: "Fix the save bugs in o/a. Out of scope: the export page. Done when saving twice keeps one record.",
  added: [{ what: "What is out of scope", why: "The planner would otherwise take in the export page." }],
  questions: ["Which release must the fix ship in?"],
  model: "m",
  usd_cost: "0.01",
};

// The New goal form, with an enrich call that answers as given; the goals it saves go into `created`.
function newGoal(enrich: () => Promise<Response>, asked: Record<string, unknown>[] = [], created: Record<string, unknown>[] = []) {
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/a" }] });
    if (url === "/v1/goals/enrich") {
      asked.push(JSON.parse(String(init?.body)));
      return enrich();
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

function description(): HTMLTextAreaElement {
  return screen.getByRole("textbox", { name: /^Description/ }) as HTMLTextAreaElement;
}

async function typeAndImprove(text: string) {
  await screen.findByRole("checkbox", { name: "o/a", checked: true });
  fireEvent.change(description(), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Improve with AI" }));
}

test("the description is replaced at once, with the open questions under it and no proposal to accept", async () => {
  const asked: Record<string, unknown>[] = [];
  newGoal(() => respond(PROPOSAL), asked);
  await typeAndImprove("Fix the save bugs");

  await screen.findByTestId("improve-done");
  expect(description().value).toBe(PROPOSAL.statement);
  expect(screen.queryByRole("button", { name: "Accept" })).toBeNull();
  expect(screen.queryByTestId("improve-proposal")).toBeNull();
  expect(screen.getByTestId("improve-questions").textContent).toContain("Which release must the fix ship in?");
  expect(asked[0]).toMatchObject({ statement: "Fix the save bugs", repos: ["o/a"] });
});

test("undo puts back the description as it was typed", async () => {
  newGoal(() => respond(PROPOSAL));
  await typeAndImprove("Fix the save bugs");
  const done = await screen.findByTestId("improve-done");

  fireEvent.click(within(done).getByRole("button", { name: "Undo" }));

  await waitFor(() => expect(description().value).toBe("Fix the save bugs"));
  expect(screen.queryByTestId("improve-done")).toBeNull();
});

test("a failed call says so, keeps the text, and the goal can still be saved", async () => {
  const created: Record<string, unknown>[] = [];
  newGoal(() => respond({ detail: "could not propose a statement: the model did not answer" }, 502), [], created);
  await typeAndImprove("Fix the save bugs");

  const failed = await screen.findByTestId("improve-failed");
  expect(failed.textContent).toContain("could not propose a statement: the model did not answer");
  expect(failed.textContent).toContain("kept as you typed it");
  expect(description().value).toBe("Fix the save bugs");
  expect(screen.queryByTestId("improve-proposal")).toBeNull();

  fireEvent.change(screen.getByRole("combobox", { name: /^Label/ }), { target: { value: "bug" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Goal page")).toBeTruthy();
  expect(created[0]).toMatchObject({ statement: "Fix the save bugs" });
});

test("on a goal's page the new description is saved at once, and a refusal says so and saves nothing", async () => {
  const accepted: unknown[] = [];
  let refuse = true;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/goals/enrich") return respond({ ...PROPOSAL, goal_id: "g-1" });
    if (url === "/v1/goals/g-1/enrich/accept") {
      accepted.push(JSON.parse(String(init?.body)));
      return refuse ? respond({ detail: "the goal is met: reopen it to change it" }, 409) : respond({ id: "g-1", statement: PROPOSAL.statement });
    }
    return respond({ detail: "not stubbed" }, 404);
  });
  const saved: string[] = [];
  render(
    <MantineProvider theme={theme}>
      <ImproveGoal
        statement="Fix the save bugs"
        context={{ goal_id: "g-1" }}
        onAccept={async (statement) => {
          const goal = await api.acceptEnrichment("g-1", statement);
          saved.push(goal.statement);
        }}
      />
    </MantineProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Improve with AI" }));
  const failed = await screen.findByTestId("improve-failed");
  expect(failed.textContent).toContain("was not saved: the goal is met");
  expect(screen.queryByTestId("improve-done")).toBeNull();

  refuse = false;
  fireEvent.click(screen.getByRole("button", { name: "Improve with AI" }));
  await screen.findByTestId("improve-done");
  expect(accepted).toEqual([{ statement: PROPOSAL.statement }, { statement: PROPOSAL.statement }]);
  expect(saved).toEqual([PROPOSAL.statement]);
});
