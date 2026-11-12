import { expect, test } from "vitest";
import type { MhEvent } from "../../api";
import { entries } from "./Timeline";
import { writingLine } from "./writing";

test("the writing pass says what it checked, what it found and whether its rewrite was posted", () => {
  expect(writingLine({ kind: "pull_request", texts: 1, findings: 0, kept: 0, reasons: [] })).toBe(
    "Writing pass on the pull request's description: nothing found",
  );
  expect(writingLine({ kind: "review", texts: 3, findings: 4, kept: 2, reasons: [] })).toBe(
    "Writing pass on the review: 4 findings, rewritten before it was posted",
  );
  expect(writingLine({ kind: "comment", texts: 1, findings: 1, kept: 0, reasons: ["the rewrite lost a code span: `x`"] })).toBe(
    "Writing pass on a comment: 1 finding, posted as written (the rewrite lost a code span: `x`)",
  );
  expect(writingLine({ kind: "review", texts: 3, findings: 5, kept: 1, reasons: ["the HTTP Gateway answered 503: no"] })).toBe(
    "Writing pass on the review: 5 findings; 1 of 3 texts rewritten, the rest posted as written (the HTTP Gateway answered 503: no)",
  );
});

test("the timeline shows the writing pass as an item", () => {
  const event: MhEvent = {
    position: 1, event_id: "1", event_type: "writing_checked", time_utc: "2026-11-11T10:00:00Z", source: "run", actor: null, run_id: null, task_id: "t",
    payload: { kind: "review", texts: 2, findings: 0, kept: 0, reasons: [] },
  };
  expect(entries([event])).toEqual([{ kind: "status", position: 1, text: "Writing pass on the review: nothing found", error: false }]);
});
