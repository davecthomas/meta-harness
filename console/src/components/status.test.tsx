import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { STATUSES, statusWords } from "./status";
import { StatusBadge } from "./ui";
import { shownStatus } from "../features/tasks/shownStatus";
import { theme } from "../theme";

// One label, meaning and color for each status, wherever it shows.

test("every status the service has reads as a plain label with what it means", () => {
  const service = ["queued", "blocked", "running", "taken_over", "ready", "review_unresolved", "closed", "failed", "canceled", "planning", "active", "paused", "stalled", "budget_exhausted", "met"];
  for (const status of service) {
    const words = statusWords(status);
    expect(words.label, status).not.toMatch(/_/);
    expect(words.meaning.length, status).toBeGreaterThan(20);
  }
  expect(statusWords("review_unresolved").label).toBe("Review unresolved");
  expect(statusWords("budget_exhausted").label).toBe("Out of budget");
  expect(statusWords("closed").label).toBe("Done");
  // States that are not the same do not look the same.
  const color = (status: string) => STATUSES[status].color;
  expect(new Set([color("queued"), color("blocked"), color("running")]).size).toBe(3);
  expect(color("closed")).not.toBe(color("canceled"));
  // One the table does not hold still reads as words.
  expect(statusWords("some_new_state")).toEqual({ label: "Some new state", meaning: "", color: "gray" });
});

test("a badge shows the label and says its meaning to a screen reader", () => {
  render(<MantineProvider theme={theme}><StatusBadge status="review_unresolved" /></MantineProvider>);
  const badge = screen.getByText("Review unresolved").closest("[data-status]")!;
  expect(badge.getAttribute("data-status")).toBe("review_unresolved");
  expect(badge.getAttribute("aria-label")).toContain("A person decides what happens next");
});

test("a working task with a pull request open is in review on every page", () => {
  expect(shownStatus({ status: "running", pr_url: "https://github.com/o/r/pull/9" })).toBe("in_review");
  expect(shownStatus({ status: "running", pr_url: null })).toBe("running");
  expect(shownStatus({ status: "ready", pr_url: "https://github.com/o/r/pull/9" })).toBe("ready");
});
