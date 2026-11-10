import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { TaskPolicyView } from "./ConfigPage";
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
