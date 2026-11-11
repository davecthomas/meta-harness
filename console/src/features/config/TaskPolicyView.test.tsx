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
  expect(screen.getByText("harness:pi-review/claude:claude-opus-5-5")).toBeTruthy();
});

function Staged() {
  const [document, setDocument] = useState<Record<string, any>>(POLICY);
  const staging: Staging = { document, original: POLICY, stage: (path, value) => setDocument(setIn(document, path, value)), reasons: () => [] };
  return (
    <>
      <TaskPolicyView policy={document} maxSandboxes={8} staging={staging} />
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
  expect(screen.getByText("changed")).toBeTruthy();
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
