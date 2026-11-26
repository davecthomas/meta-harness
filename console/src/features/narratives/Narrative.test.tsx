import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { Narrative } from "./Narrative";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

const PERSONAS = { personas: [{ id: "product", label: "Product" }, { id: "developer", label: "Developer" }], enabled: true };

function show(answers: Record<string, unknown>) {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    const body = url === "/v1/overview/personas" ? PERSONAS : answers[url];
    if (body === undefined) return Promise.resolve(new Response(JSON.stringify({ detail: "there is no narrative to show yet: open this again in a minute" }), { status: 503, headers: { "Content-Type": "application/json" } }));
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <Narrative title="Narrative" path="/v1/goals/g-1/narrative" />
    </MantineProvider>,
  );
  return asked;
}

const read = (persona: string, text: string, more: Record<string, unknown> = {}) => ({ persona, label: persona, text, written_at: "2026-11-22T10:00:00Z", stale: false, why: "", ...more });

test("a narrative is read only when asked for, for the reader picked from the planner's list", async () => {
  const asked = show({
    "/v1/goals/g-1/narrative?persona=product": read("product", "Users can now export their data."),
    "/v1/goals/g-1/narrative?persona=developer": read("developer", "The export endpoint merged in two pull requests."),
  });
  // Folded: opening the page asks for nothing, so it writes nothing.
  expect(asked).toEqual([]);

  fireEvent.click(screen.getByRole("button", { name: "Narrative" }));

  expect((await screen.findByTestId("narrative-text")).textContent).toBe("Users can now export their data.");
  fireEvent.click(screen.getByRole("radio", { name: "Developer" }));
  await waitFor(() => expect(screen.getByTestId("narrative-text").textContent).toBe("The export endpoint merged in two pull requests."));
  expect(asked).toEqual(["/v1/overview/personas", "/v1/goals/g-1/narrative?persona=product", "/v1/goals/g-1/narrative?persona=developer"]);
  // The reader chosen is the one offered next time.
  expect(localStorage.getItem("mh:narrative-persona")).toBe("developer");
});

test("Copy puts the narrative on the clipboard, and says so", async () => {
  const written: string[] = [];
  vi.stubGlobal("navigator", { clipboard: { writeText: (text: string) => { written.push(text); return Promise.resolve(); } } });
  show({ "/v1/goals/g-1/narrative?persona=product": read("product", "Users can now export their data.") });
  fireEvent.click(screen.getByRole("button", { name: "Narrative" }));

  fireEvent.click(await screen.findByRole("button", { name: "Copy" }));

  expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
  expect(written).toEqual(["Users can now export their data."]);
});

test("a narrative older than its goal says so, and one that cannot be read says what to do", async () => {
  const old = show({ "/v1/goals/g-1/narrative?persona=product": read("product", "An earlier state.", { stale: true, why: "it could not be written just now" }) });
  fireEvent.click(screen.getByRole("button", { name: "Narrative" }));
  expect((await screen.findByText(/It is older than what it describes: it could not be written just now/))).toBeTruthy();
  expect(old.length).toBe(2);
});

test("with no narrative stored and none written, the reason and what to do are shown", async () => {
  show({});
  fireEvent.click(screen.getByRole("button", { name: "Narrative" }));
  expect((await screen.findByRole("alert")).textContent).toContain("open this again in a minute");
});

test("a narrative being written says so, and appears by itself when it is done", async () => {
  let asked = 0;
  vi.stubGlobal("fetch", (url: string) => {
    const body = url === "/v1/overview/personas" ? PERSONAS : asked++ === 0 ? read("product", "", { writing: true, written_at: null }) : read("product", "Users can now export their data.", { writing: false });
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <Narrative title="Narrative" path="/v1/goals/g-1/narrative" />
    </MantineProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Narrative" }));

  expect((await screen.findByTestId("narrative-writing")).textContent).toContain("AI is writing it");
  expect(screen.queryByText(/No goal is running/)).toBeNull();
  // The page asks again by itself.
  expect((await screen.findByTestId("narrative-text", {}, { timeout: 5000 })).textContent).toBe("Users can now export their data.");
  expect(screen.queryByTestId("narrative-writing")).toBeNull();
});
