import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { theme } from "../../theme";
import { feature } from "./index";
import { HelpTopics } from "./HelpTopics";
import { TOPICS } from "./topics";

function show(path: string) {
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={[path]}>
        <HelpTopics />
        <Routes>{feature.routes.map((r) => <Route key={r.path} path={r.path} element={r.element} />)}</Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("Help starts with Why, then First principles, then The goal", () => {
  expect(TOPICS.slice(0, 3).map((t) => t.title)).toEqual(["Why", "First principles", "The goal"]);
});

test("Help opens at Why, with the way on to the next topic", () => {
  show("/help");

  expect(screen.getByRole("heading", { name: "Why" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Next: First principles" }).getAttribute("href")).toBe("/help/first-principles");
  expect(screen.queryByText(/^Back:/)).toBeNull();
});

test("a topic shows its text, and the pane lists every topic in order with this one marked", () => {
  show("/help/first-principles");

  expect(screen.getByRole("heading", { name: "First principles" })).toBeTruthy();
  expect(screen.getAllByRole("listitem")).toHaveLength(9);
  expect(screen.getByText(/Two rules are not fully built yet/)).toBeTruthy();
  const listed = screen.getAllByRole("link").filter((a) => a.getAttribute("href")?.startsWith("/help/") && !/^(Back|Next):/.test(a.textContent ?? ""));
  expect(listed.map((a) => a.textContent)).toEqual(TOPICS.map((t) => t.title));
  expect(listed[1].getAttribute("data-active")).toBe("true");
});

test("an address that names no topic opens Help at its start", () => {
  show("/help/no-such-topic");

  expect(screen.getByRole("heading", { name: "Why" })).toBeTruthy();
});

test("every topic has an address of its own and something to say", () => {
  expect(new Set(TOPICS.map((t) => t.slug)).size).toBe(TOPICS.length);
  for (const t of TOPICS) expect(t.blocks.length).toBeGreaterThan(0);
});
