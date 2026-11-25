import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { Goal } from "../../api";
import { theme } from "../../theme";
import { ActsAs } from "./ActsAs";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
const GOAL = { id: "g-1" } as Goal;
const show = (readOnly = false) => render(<MantineProvider theme={theme}><MemoryRouter><ActsAs goal={GOAL} readOnly={readOnly} /></MemoryRouter></MantineProvider>);

test("a goal with no operator says it uses the app, and a connected operator can take it over", async () => {
  const sent: string[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      sent.push(String(url));
      return json({ acts_as: "pat", login: "octo-pat", state: "connected", mine: true, can_take: false });
    }
    return json({ acts_as: null, login: null, state: null, mine: false, can_take: true });
  });
  show();
  expect((await screen.findByTestId("acts-as")).textContent).toContain("The Muxxy GitHub App");
  fireEvent.click(screen.getByRole("button", { name: "Act as me" }));
  await waitFor(() => expect(screen.getByTestId("acts-as").textContent).toContain("octo-pat (you)"));
  expect(sent).toEqual(["/v1/goals/g-1/act-as-me"]);
  expect(screen.queryByRole("button", { name: "Act as me" })).toBeNull();
});

test("a connection that no longer works says the goal stops, with the button that fixes it", async () => {
  vi.stubGlobal("fetch", () => json({ acts_as: "dave", login: "octo-dave", state: "broken", mine: true, can_take: false }));
  show();
  const row = await screen.findByTestId("acts-as");
  expect(row.textContent).toContain("GitHub refused the connection");
  expect(row.textContent).toContain("The goal stops where it next needs GitHub. Reconnect to carry on.");
  expect(screen.getByRole("button", { name: "Reconnect GitHub" })).toBeTruthy();
});

test("a refused takeover says why, and a closed goal offers none", async () => {
  vi.stubGlobal("fetch", (_url: string, init?: RequestInit) =>
    init?.method === "POST"
      ? json({ detail: "your GitHub connection cannot push to o/a, which this goal changes. Get push access to it" }, 422)
      : json({ acts_as: "dave", login: "octo-dave", state: "connected", mine: false, can_take: true }));
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Act as me" }));
  await waitFor(() => expect(screen.getByTestId("acts-as").textContent).toContain("cannot push to o/a"));
  cleanup();
  show(true);
  await screen.findByTestId("acts-as");
  expect(screen.queryByRole("button", { name: "Act as me" })).toBeNull();
});
