import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { RemovedRows, removedEntries } from "./RemovedRows";
import { TiersView } from "./TiersView";
import { setIn, type Path } from "./staging";
import type { Staging } from "./ConfigViews";
import { theme } from "../../theme";

// A removed row leaves its view at once, so the view says what was removed and puts it back on Undo.

test("a removed entry is listed with Undo, which stages it back as the active version has it", () => {
  const original = { repos: { "o/a": { default_checks: ["make test"] }, "o/b": {} } };
  const staged: [Path, unknown][] = [];
  const s: Staging = { document: { repos: { "o/b": {}, "o/new": {} } }, original, stage: (path, value) => staged.push([path, value]), reasons: () => [] };
  const rows = removedEntries(s, "repos");
  expect(rows.map((r) => r.name)).toEqual(["o/a"]);

  render(
    <MantineProvider theme={theme}>
      <RemovedRows what="repository" rows={rows} />
    </MantineProvider>,
  );
  expect(screen.getByTestId("removed-repository").textContent).toContain("Removed, not saved yet: repository o/a");
  fireEvent.click(screen.getByRole("button", { name: "Undo removing repository o/a" }));
  expect(staged).toEqual([[["repos", "o/a"], { default_checks: ["make test"] }]]);
});

test("nothing removed shows nothing", () => {
  const { container } = render(
    <MantineProvider theme={theme}>
      <RemovedRows what="skill" rows={[]} />
    </MantineProvider>,
  );
  expect(container.querySelector("[data-testid]")).toBeNull();
});

const ROUTER = {
  default_tier: "simple",
  tiers: [
    { name: "simple", models: ["harness:pi-lean/claude:a", "harness:pi-lean/claude:b"] },
    { name: "standard", models: ["harness:pi-lean/claude:c"] },
    { name: "complex", models: ["harness:pi-careful/claude:d"] },
  ],
};

// The tiers view over a staged copy that really changes, as the configuration page keeps one.
function Tiers() {
  const [document, setDocument] = useState<Record<string, any>>(ROUTER);
  return (
    <MantineProvider theme={theme}>
      <TiersView staging={{ document, original: ROUTER, stage: (path, value) => setDocument((now) => setIn(now, path, value)), reasons: () => [] }} profiles={["pi-lean", "pi-careful"]} models={["claude:a"]} />
      <output data-testid="document">{JSON.stringify(document)}</output>
    </MantineProvider>
  );
}

const staged = () => JSON.parse(screen.getByTestId("document").textContent!);

test("a removed tier goes back where it was, and a renamed tier is not called removed", () => {
  render(<Tiers />);
  fireEvent.click(screen.getByRole("button", { name: "Remove standard" }));
  expect(staged().tiers.map((t: any) => t.name)).toEqual(["simple", "complex"]);
  expect(screen.getByTestId("removed-tier").textContent).toContain("tier standard");

  fireEvent.click(screen.getByRole("button", { name: "Undo removing tier standard" }));
  expect(staged()).toEqual(ROUTER);
  expect(screen.queryByTestId("removed-tier")).toBeNull();
});

test("a removed candidate goes back in its place", () => {
  render(<Tiers />);
  fireEvent.click(screen.getByRole("button", { name: "Remove candidate 1 in simple" }));
  expect(staged().tiers[0].models).toEqual(["harness:pi-lean/claude:b"]);

  fireEvent.click(screen.getByRole("button", { name: "Undo removing candidate of simple harness:pi-lean/claude:a" }));
  expect(staged()).toEqual(ROUTER);
  expect(screen.queryByTestId("removed-candidate of simple")).toBeNull();
});
