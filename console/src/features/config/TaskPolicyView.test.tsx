import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { TaskPolicyView, type Staging } from "./ConfigViews";
import { isChanged, setIn } from "./staging";
import { theme } from "../../theme";

const POLICY = { max_parallel: 6, review: { reviewers: ["harness:pi-review/claude:claude-opus-5-5"], max_rounds: 3 } };

test("the task policy shows its parallel limit beside the VM's ceiling", () => {
  render(
    <MantineProvider theme={theme}>
      <TaskPolicyView policy={POLICY} maxSandboxes={8} />
    </MantineProvider>,
  );
  expect(screen.getByText("6, across every goal")).toBeTruthy();
  expect(screen.getByText(/the deployment's VM holds 8 sandboxes/)).toBeTruthy();
  // A reviewer reads as its two parts, and someone who only reads the policy gets no controls.
  expect(screen.getByTestId("reviewers").textContent).toContain("pi-review");
  expect(screen.getByText("claude:claude-opus-5-5")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add a reviewer" })).toBeNull();
});

function Staged() {
  const [document, setDocument] = useState<Record<string, any>>(POLICY);
  const staging: Staging = { document, original: POLICY, stage: (path, value) => setDocument(setIn(document, path, value)), reasons: () => [] };
  return (
    <>
      <TaskPolicyView policy={document} maxSandboxes={8} staging={staging} profiles={["pi-review", "pi-careful"]} models={["claude:claude-opus-5-5", "openai:gpt-5"]} />
      <output data-testid="reviewers-staged">{JSON.stringify(document.review.reviewers)}</output>
      <span>{isChanged(POLICY, document, ["max_parallel"]) ? "staged" : "not staged"}</span>
    </>
  );
}

test("the parallel limit edits in place, refused above the VM's ceiling and staged within it", async () => {
  render(
    <MantineProvider theme={theme}>
      <Staged />
    </MantineProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Edit Parallel tasks" }));
  fireEvent.change(screen.getByLabelText("Parallel tasks"), { target: { value: "12" } });
  fireEvent.keyDown(screen.getByLabelText("Parallel tasks"), { key: "Enter" });
  expect(await screen.findByText("At most 8")).toBeTruthy();

  fireEvent.change(screen.getByLabelText("Parallel tasks"), { target: { value: "7" } });
  fireEvent.keyDown(screen.getByLabelText("Parallel tasks"), { key: "Enter" });
  expect(await screen.findByText("7, across every goal")).toBeTruthy();
  await waitFor(() => expect(screen.getByText("staged")).toBeTruthy());
  expect(screen.getByText("not saved")).toBeTruthy();
});

test("the budget policy is a switch: stop at budgets, or track spend only", async () => {
  render(
    <MantineProvider theme={theme}>
      <Staged />
    </MantineProvider>,
  );
  expect(screen.getByText("A goal's budget and a task's spend limit stop work, as each says.")).toBeTruthy();
  fireEvent.click(screen.getByRole("switch", { name: /Budget policy|Stop at budgets/ }));
  expect(await screen.findByText(/No goal or task is held, refused or ended/)).toBeTruthy();
  expect(screen.getByText("Track spend only")).toBeTruthy();
});

test("the writing pass is a switch, on unless the policy turns it off", async () => {
  render(
    <MantineProvider theme={theme}>
      <Staged />
    </MantineProvider>,
  );
  expect(screen.getByText("Check what is posted for people to read")).toBeTruthy();
  fireEvent.click(screen.getByRole("switch", { name: /Writing pass|Check what is posted/ }));
  expect(await screen.findByText("Post what a run wrote as it is")).toBeTruthy();
  expect(screen.getByText(/Nothing is scanned or rewritten before it is posted/)).toBeTruthy();
});

test("a reviewer is a profile and a model picked from what the configuration has, added, reordered and removed in place", async () => {
  render(
    <MantineProvider theme={theme}>
      <Staged />
    </MantineProvider>,
  );
  const stagedNow = () => JSON.parse(screen.getByTestId("reviewers-staged").textContent!);
  // The only reviewer cannot be removed: a review needs one.
  expect((screen.getByRole("button", { name: "Remove reviewer 1" }) as HTMLButtonElement).disabled).toBe(true);

  fireEvent.click(screen.getByRole("button", { name: "Add a reviewer" }));
  expect(stagedNow()).toEqual(["harness:pi-review/claude:claude-opus-5-5", "harness:pi-review/"]);

  fireEvent.click(screen.getByRole("button", { name: "Move reviewer 2 up" }));
  expect(stagedNow()).toEqual(["harness:pi-review/", "harness:pi-review/claude:claude-opus-5-5"]);

  fireEvent.click(screen.getByRole("button", { name: "Remove reviewer 1" }));
  expect(stagedNow()).toEqual(POLICY.review.reviewers);
});
