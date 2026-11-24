import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { NextSteps, type NextStepsRead } from "./NextSteps";

afterEach(() => vi.unstubAllGlobals());

const STEP = { id: "abc123abc123", title: "Make the header logo readable", why: "The plan listed the logo's size as a gap.", statement: "Size the header's logo so its name reads.", goal: null };
const OTHER = { id: "def456def456", title: "Rename the text the services write", why: "The specification left it out.", statement: "Rename the product in pull request text.", goal: { id: "g-9", title: "Rename service text", status: "planning" } };

function read(more: Partial<NextStepsRead> = {}): NextStepsRead {
  return { steps: [], written_at: null, started: [], continues: null, budget_usd: "40.00", enabled: true, stale: false, why: "", writing: false, ...more };
}

// The panel over a fake API. answer: what each GET answers, by its address. Returns what was asked.
function show(answer: (url: string, n: number) => unknown, props: { ended: boolean; planning?: boolean }) {
  const asked: { url: string; method: string }[] = [];
  let gets = 0;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    asked.push({ url, method });
    const body = method === "POST" ? { goal_id: "g-new", already: false } : answer(url, gets++);
    const failed = body instanceof Error;
    return Promise.resolve(new Response(JSON.stringify(failed ? { detail: body.message } : body), { status: failed ? 503 : 200, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/goals/g-1"]}>
        <Routes>
          <Route path="/goals/g-1" element={<NextSteps goalId="g-1" {...props} />} />
          <Route path="/goals/g-new" element={<div>the new goal's page</div>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
  return asked;
}

test("an ended goal shows its next steps by itself, each one click from a new goal", async () => {
  const asked = show(() => read({ steps: [STEP, OTHER], written_at: "2026-11-23T10:00:00Z" }), { ended: true });

  const [first, second] = await screen.findAllByTestId("next-step");
  expect(asked[0]).toEqual({ url: "/v1/goals/g-1/next-steps", method: "GET" });
  expect(within(first).getByText("Make the header logo readable")).toBeTruthy();
  expect(within(first).getByText("The plan listed the logo's size as a gap.")).toBeTruthy();
  // A step already started shows its goal, not the button.
  expect(within(second).queryByRole("button", { name: "Start a goal from this" })).toBeNull();
  expect(within(second).getByRole("link", { name: "Open the goal started from this" }).getAttribute("href")).toBe("/goals/g-9");
  // What the one click does is said before it is pressed.
  expect(screen.getByTestId("next-steps-says").textContent).toContain("with a budget of $40.00. It is planned first: nothing runs until you approve its plan.");

  fireEvent.click(within(first).getByRole("button", { name: "Start a goal from this" }));

  expect(await screen.findByText("the new goal's page")).toBeTruthy();
  expect(asked).toContainEqual({ url: "/v1/goals/g-1/next-steps/abc123abc123/goal", method: "POST" });
  // The new goal's plan needs the operator, so what needs them is read again at once.
  const started = asked.findIndex((a) => a.method === "POST");
  expect(asked.slice(started + 1)).toContainEqual({ url: "/v1/needs", method: "GET" });
});

test("a running goal writes none until asked, then says it is working and shows them", async () => {
  const asked = show((url, n) => (url.endsWith("write=false") ? read() : n < 2 ? read({ writing: true }) : read({ steps: [STEP], written_at: "2026-11-23T10:00:00Z" })), { ended: false });

  fireEvent.click(await screen.findByRole("button", { name: "Suggest next steps now" }));

  expect(asked[0].url).toBe("/v1/goals/g-1/next-steps?write=false");
  expect((await screen.findByTestId("next-steps-writing")).textContent).toContain("which takes a few seconds");
  expect(asked[1].url).toBe("/v1/goals/g-1/next-steps");
  await waitFor(() => expect(screen.getByTestId("next-step")).toBeTruthy(), { timeout: 5000 });
});

test("an ended goal with nothing left to suggest says so, and one that cannot be read says what to do", async () => {
  show(() => read({ written_at: "2026-11-23T10:00:00Z" }), { ended: true });
  expect((await screen.findByTestId("next-steps-none")).textContent).toBe("I found nothing in this goal's records that calls for more work.");
  document.body.innerHTML = "";

  show(() => new Error("there are no next steps to show yet: ask again in a minute"), { ended: true });
  expect((await screen.findByRole("alert")).textContent).toContain("ask again in a minute");
});

test("a goal being planned says only where it came from", async () => {
  show(() => read({ continues: { id: "g-0", title: "Muxxy branding" } }), { ended: false, planning: true });
  expect((await screen.findByRole("link", { name: "Muxxy branding" })).getAttribute("href")).toBe("/goals/g-0");
  expect(screen.queryByRole("button")).toBeNull();
});

test("nothing is shown when next steps are turned off and there are none", async () => {
  const asked = show(() => read({ enabled: false }), { ended: true });
  await waitFor(() => expect(asked.length).toBe(1));
  expect(screen.queryByTestId("next-steps")).toBeNull();
});
