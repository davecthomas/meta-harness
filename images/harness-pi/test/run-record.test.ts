/**
 * Tests the run record on its own: no server, no model.
 *
 *   npm test
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { FINAL_TEXT_CHARS, normalizeCommand, RunRecord, runsCheck } from "../server/run-record.ts";

test("a command runs a check when the check, as written, is part of it", () => {
	assert.equal(runsCheck("uv run pytest -q tests", "uv run pytest -q tests"), true);
	assert.equal(runsCheck("uv sync &&  uv run   pytest -q tests 2>&1 | tail", "uv run pytest -q tests"), true);
	assert.equal(runsCheck("cd /workspace && uv run pytest -q tests", "uv run pytest -q tests", "/workspace"), true);
	assert.equal(
		runsCheck("cd /workspace/images/harness-pi && npm test", "cd images/harness-pi && npm test", "/workspace/"),
		true,
		"the workspace's own path is dropped",
	);
	assert.equal(runsCheck("uv run pytest -q tests/run_service", "uv run ruff check ."), false);
	assert.equal(runsCheck("anything", "   "), false, "an empty check matches nothing");
	assert.equal(normalizeCommand("  a \n b  "), "a b");
});

test("the final words are the last turn's text only when that turn called no tool", () => {
	const record = new RunRecord([]);
	record.turn("Done, and the checks pass.", 0);
	assert.equal(record.view().final_text, "Done, and the checks pass.");
	record.turn("Now I run the tests.", 1);
	assert.equal(record.view().final_text, null, "a later turn that called a tool replaces them");
	record.turn("   ", 0);
	assert.equal(record.view().final_text, null);
	record.turn("x".repeat(FINAL_TEXT_CHARS + 10), 0);
	assert.equal(record.view().final_text!.length, FINAL_TEXT_CHARS);
});

test("each check keeps how its last run ended, and the commands are counted", () => {
	const record = new RunRecord(["make test", "make lint"]);
	record.command("make test", true);
	record.command("ls", false);
	record.command("make test", false);
	record.fileChanged("a.py");
	record.fileChanged("a.py");
	record.fileChanged("b.py");
	record.planUpdated([{ step: "Fix it", status: "done" }]);
	assert.deepEqual(record.view(), {
		final_text: null,
		plan: [{ step: "Fix it", status: "done" }],
		files_changed: ["a.py", "b.py"],
		checks: [
			{ check: "make test", ran: true, passed: true },
			{ check: "make lint", ran: false, passed: null },
		],
		commands: { run: 3, failed: 1 },
	});
});
