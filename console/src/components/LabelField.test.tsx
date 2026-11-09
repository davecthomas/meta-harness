import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { LabelField, suggestions } from "./LabelField";
import type { LabelCount } from "../api";
import { theme } from "../theme";

const LABELS: LabelCount[] = [
  { label: "bug", total: 3, repos: { "o/a": 2, "o/b": 1 }, held: 0, held_by: [] },
  { label: "meta-harness:console", total: 2, repos: { "o/a": 2 }, held: 0, held_by: [] },
  { label: "docs", total: 4, repos: { "o/b": 4 }, held: 0, held_by: [] },
  { label: "Release", total: 1, repos: { "o/a": 1 }, held: 1, held_by: ["g-7"] },
];

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function Harness({ repos }: { repos: string[] }) {
  const [label, setLabel] = useState("");
  return (
    <MantineProvider theme={theme}>
      <LabelField value={label} onChange={setLabel} repos={repos} />
      <output data-testid="value">{label}</output>
    </MantineProvider>
  );
}

afterEach(() => vi.unstubAllGlobals());

test("suggestions are the picked repos' labels, counted in those repos, most first", () => {
  expect(suggestions(LABELS, []).map((s) => [s.label, s.count])).toEqual([
    ["docs", 4],
    ["bug", 3],
    ["meta-harness:console", 2],
    ["Release", 1],
  ]);
  const inA = suggestions(LABELS, ["o/a"]);
  expect(inA.map((s) => [s.label, s.count])).toEqual([
    ["bug", 2],
    ["meta-harness:console", 2],
    ["Release", 1],
  ]);
  expect(inA.find((s) => s.label === "Release")).toMatchObject({ allHeld: true, heldBy: ["g-7"] });
  expect(inA.find((s) => s.label === "bug")).toMatchObject({ allHeld: false, repos: ["o/a"] });
});

test("typing suggests matching labels anywhere in the name, ignoring case, and picking one fills the field", async () => {
  vi.stubGlobal("fetch", (url: string) => (url === "/v1/labels" ? respond({ repos: ["o/a", "o/b"], labels: LABELS }) : respond({}, 404)));
  render(<Harness repos={["o/a"]} />);

  const input = screen.getByRole("combobox", { name: /^Label/ });
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "CONS" } });

  const option = await screen.findByText("meta-harness:console");
  expect(screen.getByText(/2 open · o\/a/)).toBeTruthy();
  expect(screen.queryByText("docs")).toBeNull();
  fireEvent.click(option);
  expect(screen.getByTestId("value").textContent).toBe("meta-harness:console");
  expect(screen.queryByText("No open issue has this label yet.")).toBeNull();
});

test("a label held entirely by another goal is marked", async () => {
  vi.stubGlobal("fetch", (url: string) => (url === "/v1/labels" ? respond({ repos: ["o/a"], labels: LABELS }) : respond({}, 404)));
  render(<Harness repos={[]} />);

  const input = screen.getByRole("combobox", { name: /^Label/ });
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "rel" } });

  expect(await screen.findByText(/all held by goal g-7/)).toBeTruthy();
});

test("a label no open issue has is allowed, and the field says so", async () => {
  vi.stubGlobal("fetch", (url: string) => (url === "/v1/labels" ? respond({ repos: ["o/a"], labels: LABELS }) : respond({}, 404)));
  render(<Harness repos={["o/a"]} />);

  fireEvent.change(screen.getByRole("combobox", { name: /^Label/ }), { target: { value: "bgu" } });

  expect(await screen.findByText("No open issue has this label yet.")).toBeTruthy();
  expect(screen.getByTestId("value").textContent).toBe("bgu");
});

test("when the labels can't be read, the field is plain text and says why", async () => {
  vi.stubGlobal("fetch", () => respond({ detail: "GitHub is down" }, 502));
  render(<Harness repos={[]} />);

  expect(await screen.findByText(/Labels could not be loaded/)).toBeTruthy();
  fireEvent.change(screen.getByRole("combobox", { name: /^Label/ }), { target: { value: "bug" } });
  expect(screen.getByTestId("value").textContent).toBe("bug");
});
