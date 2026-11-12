import { expect, test } from "vitest";
import { supervisorTrouble } from "./SupervisorHealth";

const now = Date.parse("2026-11-12T10:00:00Z");

test("a supervisor that is keeping up has no trouble to say", () => {
  expect(supervisorTrouble({ every_seconds: 60, last_pass: { at: "2026-11-12T09:59:30Z", error: null, errors: [] } }, now)).toEqual([]);
});

test("a supervisor that is off, late, failing or has not run says so", () => {
  expect(supervisorTrouble({ every_seconds: 0, last_pass: null }, now)[0]).toContain("turned off");
  expect(supervisorTrouble({ every_seconds: 60, last_pass: null }, now)).toEqual(["The supervisor has not finished a pass yet."]);
  expect(supervisorTrouble({ every_seconds: 60, last_pass: { at: "2026-11-12T09:40:00Z" } }, now)).toEqual([
    "The supervisor last finished a pass 20 minutes ago. It should run every 60 seconds.",
  ]);
  const failing = supervisorTrouble({ every_seconds: 60, last_pass: { at: "2026-11-12T09:59:30Z", error: "TaskApiError: 503", errors: ["t-1: KeyError"] } }, now);
  expect(failing).toEqual(["The supervisor's last pass failed: TaskApiError: 503", "It could not look at a task: t-1: KeyError"]);
});
