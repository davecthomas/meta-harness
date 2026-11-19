import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { PlannerUpdates, since } from "./PlannerUpdates";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

const NOW = "2026-11-19T12:00:00Z";
const LATEST = {
  number: 2,
  headline: "Two of three tasks are in review.",
  body: "The store and API tasks opened pull requests. The UI task waits on the API.",
  needs_you: [{ what: "Review the API pull request.", ref: "github:o/r#12" }],
  next: "The UI task starts when the API merges.",
  created_at: NOW,
};
const FIRST = { number: 1, headline: "The plan is ready for you.", body: "Three tasks, in order.", needs_you: [], next: "", created_at: "2026-11-19T11:00:00Z" };

// The panel over a fake API. reads: what each GET answers, the last one repeating. Returns the POSTs made.
function show(reads: unknown[], open = true, ask: () => Promise<Response> = () => respond({ update: LATEST })) {
  const posts: string[] = [];
  let read = 0;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      posts.push(url);
      return ask();
    }
    return respond(reads[Math.min(read++, reads.length - 1)]);
  });
  render(
    <MantineProvider theme={theme}>
      <PlannerUpdates goalId="g-1" open={open} />
    </MantineProvider>,
  );
  return posts;
}

test("the latest update leads with its headline, what needs the operator and what is next", async () => {
  show([{ updates: [LATEST, FIRST], enabled: true, error: null }]);

  expect((await screen.findByTestId("planner-headline")).textContent).toBe("Two of three tasks are in review.");
  expect(screen.getByText(/The store and API tasks opened pull requests/)).toBeTruthy();
  const needs = screen.getByTestId("planner-needs-you");
  expect(needs.textContent).toContain("Needs you");
  expect(needs.textContent).toContain("Review the API pull request.");
  expect(needs.querySelector("a")?.getAttribute("href")).toBe("https://github.com/o/r/issues/12");
  expect(screen.getByText("The UI task starts when the API merges.")).toBeTruthy();

  // The ones before it are a click away.
  fireEvent.click(screen.getByRole("button", { name: "1 earlier update" }));
  expect(await screen.findByText("The plan is ready for you.")).toBeTruthy();
});

test("an open goal with no update yet says one is coming, and asking writes one now", async () => {
  const posts = show([{ updates: [], enabled: true, error: null }, { updates: [LATEST], enabled: true, error: null }]);

  expect(await screen.findByText(/Its first update is written within a couple of minutes/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Ask for an update" }));

  await waitFor(() => expect(posts).toEqual(["/v1/goals/g-1/updates"]));
  expect((await screen.findByTestId("planner-headline")).textContent).toBe("Two of three tasks are in review.");
});

test("an update that could not be written says why and what to do, and keeps the last one", async () => {
  show([{ updates: [LATEST], enabled: true, error: "UpdateError: the HTTP Gateway answered 503" }], true, () =>
    respond({ detail: "the update could not be written: the HTTP Gateway answered 503. Ask again in a moment" }, 502),
  );

  expect(await screen.findByText(/could not write its latest update \(UpdateError: the HTTP Gateway answered 503\)/)).toBeTruthy();
  expect(screen.getByTestId("planner-headline")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Ask for an update" }));
  expect((await screen.findByTestId("planner-update-refused", {}, { timeout: 60000 })).textContent).toContain("Ask again in a moment");
}, 70000);

test("nothing is shown with updates off, or on a closed goal that never had one", async () => {
  show([{ updates: [LATEST], enabled: false, error: null }]);
  await waitFor(() => expect(screen.queryByTestId("planner-updates")).toBeNull());
});

test("a closed goal keeps its last update and offers no new one", async () => {
  show([{ updates: [LATEST], enabled: true, error: null }], false);

  expect(await screen.findByTestId("planner-headline")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Ask for an update" })).toBeNull();
});

test("an update's age reads as a person would say it", () => {
  const now = Date.parse(NOW);
  expect(since(NOW, now)).toBe("just now");
  expect(since("2026-11-19T11:59:00Z", now)).toBe("1 minute ago");
  expect(since("2026-11-19T11:15:00Z", now)).toBe("45 minutes ago");
  expect(since("2026-11-19T09:00:00Z", now)).toBe("3 hours ago");
});
