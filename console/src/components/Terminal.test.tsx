import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test } from "vitest";
import type { Line } from "../terminal";
import { Terminal } from "./Terminal";

beforeEach(() => window.localStorage.clear());
afterEach(() => window.localStorage.clear());

const prompt = (position: number, template: string, detail: string): Line =>
  ({ position, kind: "info", text: `10:00:00 prompt (${template})`, detail });
const turn = (position: number, n: number): Line => ({ position, kind: "turn", text: `── turn ${n} ──` });

const openOf = (template: string) => screen.getByText(`10:00:00 prompt (${template})`).closest("details")!.open;

test("a run's prompt shows expanded at the top of its run, with its full text", () => {
  render(<Terminal lines={[prompt(1, "repo-task-v1", "Fix the bug."), turn(2, 1)]} />);
  const log = screen.getByRole("log");
  expect(log.firstElementChild!.tagName).toBe("DETAILS");
  expect(openOf("repo-task-v1")).toBe(true);
  expect(log.querySelector("details pre")!.textContent).toBe("Fix the bug.");
});

test("a collapsed prompt stays collapsed as the run's output grows", () => {
  const first = [prompt(1, "repo-task-v1", "Fix the bug."), turn(2, 1)];
  const { rerender } = render(<Terminal lines={first} />);
  fireEvent.click(screen.getByText("10:00:00 prompt (repo-task-v1)"));
  expect(openOf("repo-task-v1")).toBe(false);

  rerender(<Terminal lines={[...first, turn(3, 2), turn(4, 3)]} />);
  expect(openOf("repo-task-v1")).toBe(false);

  // Opened again, it stays open.
  fireEvent.click(screen.getByText("10:00:00 prompt (repo-task-v1)"));
  expect(openOf("repo-task-v1")).toBe(true);
});

test("a collapsed prompt stays collapsed for its run in another terminal, and other runs' prompts do not", () => {
  const lines = [prompt(1, "repo-task-v1", "Fix the bug."), turn(2, 1), prompt(5, "review-v1", "Review it."), turn(6, 1)];
  render(<Terminal lines={lines} />);
  fireEvent.click(screen.getByText("10:00:00 prompt (repo-task-v1)"));
  cleanup();

  // The same run's prompt, shown again (a reload, a tile): still collapsed. The review run's is its own.
  render(<Terminal lines={lines} />);
  expect(openOf("repo-task-v1")).toBe(false);
  expect(openOf("review-v1")).toBe(true);
  cleanup();

  // A fix run that starts later shows its prompt expanded.
  render(<Terminal lines={[...lines, prompt(9, "fix-v1", "Fix the findings.")]} />);
  expect(openOf("fix-v1")).toBe(true);
});
