/**
 * Tests the run record on its own: no server, no model.
 *
 *   npm test
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { FINAL_TEXT_CHARS, matchCheck, normalizeCommand, RunRecord, runsCheck } from "../server/run-record.ts";

test("a command runs a check when the check's steps are whole steps of it", () => {
	assert.equal(runsCheck("uv run pytest -q tests", "uv run pytest -q tests"), true);
	assert.equal(runsCheck("uv sync &&  uv run   pytest -q tests", "uv run pytest -q tests"), true);
	assert.equal(runsCheck("uv run pytest -q tests/run_service", "uv run pytest -q tests"), false, "a narrower run");
	assert.equal(runsCheck("echo uv run pytest -q tests", "uv run pytest -q tests"), false, "a print of it");
	assert.equal(runsCheck("echo 'uv run pytest -q tests'", "uv run pytest -q tests"), false);
	assert.equal(runsCheck("cd images/harness-pi && uv run pytest -q tests", "uv run pytest -q tests"), false, "another dir");
	assert.equal(runsCheck("cd images/harness-pi && npm ci && npm test", "cd images/harness-pi && npm ci"), true);
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

test("what a command's exit status proves about a check depends on how the command holds it", () => {
	const check = "uv run pytest -q tests";
	assert.equal(matchCheck("uv run pytest -q tests", check), "exact");
	assert.equal(matchCheck("cd /workspace && uv run pytest -q tests", check, "/workspace"), "exact");
	assert.equal(matchCheck("uv sync && uv run pytest -q tests", check), "chained");
	assert.equal(matchCheck("uv run pytest -q tests 2>&1 && uv run ruff check .", "uv run ruff check ."), "chained");
	assert.equal(matchCheck("uv sync && uv run pytest -q tests | tail", check), "opaque", "a pipe hides its status");
	assert.equal(matchCheck("uv run pytest -q tests && echo ok | tail", check), "opaque");
	assert.equal(matchCheck("uv run pytest -q tests || true", check), "opaque");
	assert.equal(matchCheck("uv sync\nuv run pytest -q tests", check), "opaque", "a newline separates steps");
	assert.equal(matchCheck("echo uv run pytest -q tests | tail", check), "none");
	assert.equal(matchCheck("uv run pytest -q tests && make x || true", check), "opaque");
	assert.equal(matchCheck("uv run ruff check .; uv run pytest -q tests && ls", check), "opaque");
	assert.equal(matchCheck("uv run pytest -q tests && ls &", check), "opaque");
});

test("a chain proves its checks passed only when it passed, and proves none failed", () => {
	const checks = ["uv run pytest -q tests", "uv run ruff check .", "cd images/harness-pi && npm ci && npm test"];
	const record = new RunRecord(checks, "/workspace");
	record.command("uv run pytest -q tests/run_service", false);
	record.command("echo uv run ruff check .", false);
	assert.deepEqual(
		record.view().checks.map((c) => c.ran),
		[false, false, false],
		"neither a narrower run nor a print counts",
	);

	record.command("uv run pytest -q tests && uv run ruff check .", true);
	assert.deepEqual(record.view().checks.slice(0, 2), [
		{ check: checks[0], ran: true, passed: null },
		{ check: checks[1], ran: true, passed: null },
	]);

	record.command("cd /workspace && uv run pytest -q tests && uv run ruff check .", false);
	record.command("cd /workspace/images/harness-pi && npm ci && npm test", false);
	assert.deepEqual(
		record.view().checks.map((c) => c.passed),
		[true, true, true],
	);

	record.command("uv run ruff check .", true);
	record.command("uv run pytest -q tests && echo done | tee log", false);
	assert.deepEqual(record.view().checks.slice(0, 2), [
		{ check: checks[0], ran: true, passed: null },
		{ check: checks[1], ran: true, passed: false },
	]);
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
