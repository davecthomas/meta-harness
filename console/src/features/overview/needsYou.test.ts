import { expect, test } from "vitest";
import { needsYouNote } from "./needsYou";

test("the Needs you number says what it is made of", () => {
  const tasks = [{ status: "failed" }, { status: "failed" }, { status: "ready" }, { status: "review_unresolved" }];
  expect(needsYouNote(tasks, 1, 1, 6)).toBe(
    "2 failed, 1 with an unresolved review, 1 ready to merge, 1 held for budget or review, 1 goal past its budget. Each says what to do.",
  );
  expect(needsYouNote(tasks.slice(0, 2), 0, 0, 30)).toBe("2 failed, 28 more. Each says what to do.");
  expect(needsYouNote([], 0, 0, 0)).toBe("Nothing waits on you.");
  expect(needsYouNote([], 0, 0, null)).toBe("");
});
