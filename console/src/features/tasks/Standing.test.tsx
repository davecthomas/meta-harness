import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import type { TaskStanding } from "../../api";
import { theme } from "../../theme";
import { Standing, standingLine } from "./Standing";

const base: TaskStanding = {
  owner: "operator", situation: "ready", reason: "its review is clean and its pull request is open, and nothing merges it",
  ask: "Merge the pull request, on the code host or with Merge on the task\u2019s page.", since: "2026-11-11T10:00:00Z",
  checked_at: "2026-11-11T12:00:00Z", attempts: 0, last_error: null,
};

test("a task an operator has says why and what to do", () => {
  render(<MantineProvider theme={theme}><Standing standing={base} /></MantineProvider>);
  const shown = screen.getByTestId("standing").textContent ?? "";
  expect(shown).toContain("You");
  expect(shown).toContain("Its review is clean and its pull request is open, and nothing merges it. Merge the pull request");
  expect(shown).toContain("Since ");
});

test("a task the supervisor is moving says what it found and how its tries went", () => {
  const moving: TaskStanding = { ...base, owner: "supervisor", situation: "no_workflow", reason: "it has no RunTask workflow", ask: null, attempts: 2, last_error: "the task service is down" };
  expect(standingLine(moving)).toBe("It has no RunTask workflow.");
  render(<MantineProvider theme={theme}><Standing standing={moving} /></MantineProvider>);
  const shown = screen.getByTestId("standing").textContent ?? "";
  expect(shown).toContain("The supervisor");
  expect(shown).toContain("It has acted 2 times. Its last try failed: the task service is down");
});
