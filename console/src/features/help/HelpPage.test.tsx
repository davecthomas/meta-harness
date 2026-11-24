import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { expect, test } from "vitest";
import { theme } from "../../theme";
import { feature } from "./index";
import { TOPICS } from "./topics";

function Where() {
  const { pathname, hash } = useLocation();
  return <span data-testid="where">{pathname}{hash}</span>;
}

function show(path: string) {
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={[path]}>
        <Where />
        <Routes>{feature.routes.map((r) => <Route key={r.path} path={r.path} element={r.element} />)}</Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("Help is one page that starts with Why, then First principles, then The goal", () => {
  show("/help");

  const titles = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
  expect(titles).toEqual(TOPICS.map((t) => t.title));
  expect(titles.slice(0, 3)).toEqual(["Why", "First principles", "The goal"]);
  expect(titles).not.toContain("When it asks you");
});

test("the page links to each of its sections, and each section can be linked to", () => {
  show("/help");

  const links = Array.from(screen.getByTestId("help-sections").querySelectorAll("a"));
  expect(links.map((a) => a.getAttribute("href"))).toEqual(TOPICS.map((t) => `#${t.slug}`));
  for (const t of TOPICS) expect(document.getElementById(t.slug)).toBeTruthy();
});

test("the introduction names the product Muxxy AI and no topic calls it the meta-harness", () => {
  show("/help");
  expect(screen.getByText(/^Muxxy AI clears most of those stops/)).toBeTruthy();
  expect(JSON.stringify(TOPICS)).not.toMatch(/meta-harness/i);
});

test("the left pane has one Help entry, with no list under it", () => {
  expect(feature.nav).toHaveLength(1);
  expect(feature.nav[0].history).toBeUndefined();
});

test("an address from when a section had its own page opens the one page at that section", () => {
  show("/help/spend-limits");
  expect(screen.getByTestId("where").textContent).toBe("/help#spend-limits");
});

test("an address that names no section opens Help at its start", () => {
  show("/help/no-such-topic");
  expect(screen.getByTestId("where").textContent).toBe("/help");
});
