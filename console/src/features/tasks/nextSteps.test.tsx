import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import type { Diagnosis } from "./diagnosis";
import { nextSteps } from "./nextSteps";
import { Remedies } from "./Remedies";

afterEach(() => vi.unstubAllGlobals());

const ROUNDS: Diagnosis = { kind: "fix_rounds", summary: "s", cause: "c", fix: "f", remedies: ["task", "retry"], detail: "" };
const LIMIT: Diagnosis = { kind: "limit", summary: "s", cause: "c", fix: "f", remedies: ["retry", "config"], detail: "" };
const GATEWAY: Diagnosis = { kind: "model", summary: "s", cause: "c", fix: "f", remedies: ["retry", "task"], detail: "" };
const PR = "https://github.com/o/r/pull/7";

test("a stronger model is recommended first, then the last review or the limit once it has had its try", () => {
  expect(nextSteps(ROUNDS, { status: "review_unresolved", pr_url: PR })).toEqual({ stronger: true, merge: true, recommended: "stronger" });
  expect(nextSteps(ROUNDS, { status: "review_unresolved", pr_url: PR, complexity: "high" })).toEqual({ stronger: true, merge: true, recommended: "review" });
  expect(nextSteps(LIMIT, { status: "failed" })).toEqual({ stronger: true, merge: false, recommended: "stronger" });
  expect(nextSteps(LIMIT, { status: "failed", complexity: "high" })).toEqual({ stronger: true, merge: false, recommended: "config" });
  // A provider outage is not the work: a stronger model is no help, so nothing is offered beyond the retry.
  expect(nextSteps(GATEWAY, { status: "failed" })).toEqual({ stronger: false, merge: false, recommended: null });
});

function show(steps: { status: string; complexity?: string | null; pr_url?: string | null }, diagnosis: Diagnosis = ROUNDS) {
  const asked: { url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    asked.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    return Promise.resolve(new Response(JSON.stringify({ id: "t-1", status: "queued" }), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <Remedies diagnosis={diagnosis} tasks={[{ id: "t-1", max_usd: "10.00" }]} steps={steps} />
      </MemoryRouter>
    </MantineProvider>,
  );
  return asked;
}

test("an unsettled review offers a stronger model as recommended, the same model, a merge as it is and the last review", async () => {
  const asked = show({ status: "review_unresolved", pr_url: PR });

  expect(screen.getByRole("button", { name: "Retry with a stronger model (recommended)" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Retry with the same model" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Read the last review" }).getAttribute("href")).toBe(PR);
  expect(screen.getByText("Each retry can spend up to $10.00.")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Retry with a stronger model (recommended)" }));
  await waitFor(() => expect(asked).toHaveLength(1));
  expect(asked[0]).toEqual({ url: "/v1/tasks/t-1/retry", body: { max_usd: 10, complexity: "high" } });
});

test("Merge as it is asks first and says the findings stay unfixed", async () => {
  const asked = show({ status: "review_unresolved", pr_url: PR, complexity: "high" });

  expect(screen.getByRole("link", { name: "Read the last review (recommended)" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Merge as it is" }));
  const dialog = within(await screen.findByRole("dialog"));
  expect(dialog.getByText(/The last review's findings stay unfixed/)).toBeTruthy();
  expect(asked).toHaveLength(0);
  fireEvent.click(dialog.getByRole("button", { name: "Merge as it is" }));
  await waitFor(() => expect(asked.map((a) => a.url)).toEqual(["/v1/tasks/t-1/merge"]));
});

test("a failure a stronger model cannot help keeps its one Retry", () => {
  show({ status: "failed" }, GATEWAY);
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /stronger model/ })).toBeNull();
});
