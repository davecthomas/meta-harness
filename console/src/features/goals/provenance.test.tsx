import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme";
import { ProvenanceEdit, provenanceChoice, provenanceSummary, provenanceValue, type ProvenanceGoal } from "./provenance";

function renderIn(element: React.ReactElement) {
  return render(<MantineProvider theme={theme}>{element}</MantineProvider>);
}

const goal = (provenance: boolean | null | undefined) => ({ id: "g-1", status: "active", provenance }) as unknown as ProvenanceGoal;

test("a goal's provenance setting reads as one of three choices, and the summary names only one it set", () => {
  expect([true, false, null, undefined].map(provenanceChoice)).toEqual(["on", "off", "configured", "configured"]);
  expect(["on", "off", "configured", null].map(provenanceValue)).toEqual([true, false, null, null]);
  expect(provenanceSummary(true)).toEqual(["provenance headers on"]);
  expect(provenanceSummary(null)).toEqual([]);
});

test("choosing Off saves provenance false, and As configured saves null", async () => {
  const saved: unknown[] = [];
  const { unmount } = renderIn(<ProvenanceEdit goal={goal(null)} readOnly={false} save={async (f) => saved.push(f)} />);
  expect(screen.getByText("As configured: the deployment's setting for each repo")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Edit Provenance headers" }));
  fireEvent.click(await screen.findByRole("option", { name: "Off: new files get no header", hidden: true }));
  await waitFor(() => expect(saved).toEqual([{ provenance: false }]));
  unmount();

  renderIn(<ProvenanceEdit goal={goal(true)} readOnly={false} save={async (f) => saved.push(f)} />);
  fireEvent.click(screen.getByRole("button", { name: "Edit Provenance headers" }));
  fireEvent.click(await screen.findByRole("option", { name: /^As configured/, hidden: true }));
  await waitFor(() => expect(saved).toEqual([{ provenance: false }, { provenance: null }]));
});

test("a closed goal shows its provenance setting without a way to change it", () => {
  renderIn(<ProvenanceEdit goal={goal(false)} readOnly save={async () => undefined} />);
  expect(screen.getByText("Off: new files get no header")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Edit Provenance headers" })).toBeNull();
});
