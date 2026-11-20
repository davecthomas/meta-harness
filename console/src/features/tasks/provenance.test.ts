import { expect, test } from "vitest";
import type { MhEvent } from "../../api";
import { STEP_EVENTS } from "./path";
import { provenanceLine } from "./provenance";
import { entries } from "./Timeline";

test("the provenance line says how many new files got a header, and names the first few", () => {
  expect(provenanceLine({ run_id: "task-1-1", count: 1, files: ["src/a.py"] })).toBe(
    "Provenance header added to 1 file created by run task-1-1: src/a.py",
  );
  expect(provenanceLine({ run_id: "task-1-2", count: 5, files: ["a.py", "b.ts", "c.md", "d.sql", "e.css"] })).toBe(
    "Provenance header added to 5 files created by run task-1-2: a.py, b.ts, c.md, and 2 more",
  );
  // More files than the event lists: the count says how many there were.
  expect(provenanceLine({ run_id: "r", count: 250, files: ["a.py"] })).toBe("Provenance header added to 250 files created by run r: a.py, and 249 more");
});

test("the timeline and the task's path show the provenance headers a run's files got", () => {
  const event: MhEvent = {
    position: 4, event_id: "4", event_type: "provenance_added", time_utc: "2026-11-20T10:00:00Z", source: "run", actor: null, run_id: "r-1", task_id: "t",
    payload: { run_id: "r-1", count: 2, files: ["x.py", "y.ts"] },
  };
  expect(entries([event])).toEqual([{ kind: "status", position: 4, text: "Provenance header added to 2 files created by run r-1: x.py, y.ts", error: false }]);
  expect(STEP_EVENTS).toContain("provenance_added");
});
