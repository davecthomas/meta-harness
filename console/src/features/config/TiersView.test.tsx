import { fireEvent, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { TiersView } from "./TiersView";
import { theme } from "../../theme";

// Tiers are added, removed and put in order in the view itself, with no JSON to edit.

const ROUTER = {
  default_tier: "simple",
  tiers: [
    { name: "simple", thinking: "low", models: ["harness:pi-lean/claude:claude-haiku-5"] },
    { name: "standard", thinking: "medium", models: ["harness:pi-lean/claude:claude-sonnet-5"] },
    { name: "complex", thinking: "high", models: ["harness:pi-careful/claude:claude-opus-5"] },
  ],
};

function show(document: Record<string, any> = ROUTER, readOnly = false) {
  const staged: [unknown[], any][] = [];
  render(
    <MantineProvider theme={theme}>
      <TiersView
        staging={{ document, original: ROUTER, stage: (path, value) => staged.push([path, value]), reasons: () => [], readOnly }}
        profiles={["pi-lean", "pi-careful"]}
        models={["claude:claude-sonnet-5"]}
      />
    </MantineProvider>,
  );
  return staged;
}

const names = (value: any) => (value as { name: string }[]).map((t) => t.name);

test("a tier is added last with a name no other has, and is marked not saved", () => {
  const staged = show({ ...ROUTER, tiers: [...ROUTER.tiers, { name: "tier-4", models: [] }] });
  expect(screen.getByText(/tried on its tier's candidates in order/)).toBeTruthy();
  expect(screen.getByTestId("tier-controls-tier-4").textContent).toContain("not saved");
  expect(screen.getByTestId("tier-controls-simple").textContent).not.toContain("not saved");

  fireEvent.click(screen.getByRole("button", { name: "Add a tier" }));
  const [path, value] = staged[0];
  expect(path).toEqual(["tiers"]);
  expect(names(value)).toEqual(["simple", "standard", "complex", "tier-4", "tier-5"]);
  expect(value.at(-1)).toEqual({ name: "tier-5", description: "", thinking: "medium", models: [] });
});

test("a tier moves earlier or later as one change, and the first and last cannot move past the ends", () => {
  const staged = show();
  expect((screen.getByRole("button", { name: "Move simple earlier" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Move complex later" }) as HTMLButtonElement).disabled).toBe(true);

  fireEvent.click(screen.getByRole("button", { name: "Move complex earlier" }));
  fireEvent.click(screen.getByRole("button", { name: "Move simple later" }));
  expect(staged.map(([path, value]) => [path, names(value)])).toEqual([
    [["tiers"], ["simple", "complex", "standard"]],
    [["tiers"], ["standard", "simple", "complex"]],
  ]);
});

test("a tier is removed, but not the default one, and someone who only reads sees no controls", () => {
  const staged = show();
  expect((screen.getByRole("button", { name: "Remove simple" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Remove standard" }));
  expect(names(staged[0][1])).toEqual(["simple", "complex"]);
});

test("someone who only reads the configuration sees no way to change the tiers", () => {
  show(ROUTER, true);
  expect(screen.queryByRole("button", { name: "Add a tier" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Remove standard" })).toBeNull();
});

test("with no tier it says no task can be routed and what to do", () => {
  show({ tiers: [] });
  expect(screen.getByTestId("no-tiers").textContent).toContain("Add one, give it a candidate, and make it the default tier");
});
