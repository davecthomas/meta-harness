/**
 * What the harness server records about a run, beside its events, for when the run's own summary is missing
 * or unusable: its final words, its last plan, the files it wrote or edited, and how each acceptance check
 * ended the last time a command ran it. `GET /record` returns it once the run has ended, and the control plane
 * writes a summary from it when the run left none it can use.
 *
 * Only facts are recorded here. Deciding whether the run's own summary is usable, and the words of a summary
 * built from the record, are the control plane's.
 */

import type { PlanStep } from "../extension/owned-tools.ts";

/** A final text over this many characters is cut, so a runaway reply cannot bloat the record. */
export const FINAL_TEXT_CHARS = 20_000;

/**
 * How one acceptance check ended. `ran` is false when no command ran it, and `passed` is then null. `ran` is
 * true with `passed` null when a command ran the check but its exit status cannot prove how the check ended:
 * a chain that failed, or one whose operators hide a step's status.
 */
export interface CheckOutcome {
	check: string;
	ran: boolean;
	passed: boolean | null;
}

export interface RunRecordView {
	/** The text of the run's last turn when that turn called no tool: the run's own summary. Null otherwise. */
	final_text: string | null;
	/** update_plan's last checklist. Empty when the run kept no plan. */
	plan: PlanStep[];
	/** The paths the write and edit tools changed, in the order first changed. */
	files_changed: string[];
	/** Each of the run's acceptance checks, in the order the run was given them. */
	checks: CheckOutcome[];
	/** How many bash commands the run ran, and how many of them failed. */
	commands: { run: number; failed: number };
}

/** A command with its whitespace collapsed, and the workspace's own path dropped, so it compares to a check as written. */
export function normalizeCommand(command: string, workspaceDir = ""): string {
	let text = command.replace(/\s+/g, " ").trim();
	if (workspaceDir) {
		const dir = workspaceDir.replace(/\/+$/, "");
		text = text.split(`cd ${dir} && `).join("").split(`cd ${dir}; `).join("").split(`${dir}/`).join("");
	}
	return text;
}

/**
 * The shell's step separators: `&&`, `||`, `;`, `|` and a lone `&`. A newline is read as `;` before this.
 * `2>&1`, `&>` and the like are redirections, not separators.
 */
const SEPARATORS = /&&|\|\||;|\||(?<![>&<0-9])&(?![&>])/;

/**
 * What, besides `&&`, can run a step without its status deciding the command's: every other separator, and
 * subshell or substitution syntax.
 */
const HIDING_OPERATORS = /\|\||;|\||(?<![>&<0-9])&(?![&>])|`|\$\(|[()]/;

/** A command's steps, split on every separator, each trimmed. */
function steps(command: string): string[] {
	return command.split(SEPARATORS).map((step) => step.trim());
}

/**
 * Whether the command runs the check, and what its exit status proves about it.
 *
 * The command runs the check when the check's steps appear, whole and in order, among the
 * command's steps, with no `cd` before them that the check does not make itself. A step that only prints or
 * extends the check, like `echo <check>` or `<check>/run_service`, does not match.
 *
 * - `"none"`: the command does not run the check.
 * - `"exact"`: the command is the check, so its exit status is the check's.
 * - `"chained"`: the command is a plain `&&` chain holding the check. A zero exit proves every step ran and
 *   passed; a failure does not say which step failed or whether the check ran at all.
 * - `"opaque"`: the command holds the check, but other operators hide its steps' status.
 */
export function matchCheck(command: string, check: string, workspaceDir = ""): "none" | "exact" | "chained" | "opaque" {
	const wanted = steps(normalizeCommand(check));
	if (wanted.every((step) => !step)) return "none";
	const normalized = normalizeCommand(command.trim().replace(/\n/g, " ; "), workspaceDir);
	const have = steps(normalized);
	const at = findRun(have, wanted);
	if (at < 0) return "none";
	if (have.slice(0, at).some((step) => /^(cd|pushd)(\s|$)/.test(step))) return "none";
	if (HIDING_OPERATORS.test(normalized)) return "opaque";
	return have.length === wanted.length ? "exact" : "chained";
}

/** Where `wanted` starts as a contiguous run of whole steps in `have`, or -1. */
function findRun(have: string[], wanted: string[]): number {
	for (let i = 0; i + wanted.length <= have.length; i += 1) {
		if (wanted.every((step, j) => have[i + j] === step)) return i;
	}
	return -1;
}

/** True when the command runs the check. */
export function runsCheck(command: string, check: string, workspaceDir = ""): boolean {
	return matchCheck(command, check, workspaceDir) !== "none";
}

export class RunRecord {
	private finalText: string | null = null;
	private plan: PlanStep[] = [];
	private readonly files: string[] = [];
	private readonly checks: CheckOutcome[];
	private commandsRun = 0;
	private commandsFailed = 0;
	private readonly workspaceDir: string;

	constructor(acceptanceChecks: string[], workspaceDir = "") {
		this.checks = acceptanceChecks.map((check) => ({ check, ran: false, passed: null }));
		this.workspaceDir = workspaceDir;
	}

	/** One finished turn: its text counts as the run's final words only when the turn called no tool. */
	turn(text: string, toolCalls: number): void {
		const trimmed = text.trim();
		this.finalText = toolCalls === 0 && trimmed ? trimmed.slice(0, FINAL_TEXT_CHARS) : null;
	}

	planUpdated(steps: PlanStep[]): void {
		this.plan = steps.map((step) => ({ step: step.step, status: step.status }));
	}

	fileChanged(path: string): void {
		if (!this.files.includes(path)) this.files.push(path);
	}

	/**
	 * One bash command that ended. A later run of a check replaces how an earlier one ended, even when the later
	 * run's result is unknown, since the earlier result may no longer hold.
	 */
	command(command: string, failed: boolean): void {
		this.commandsRun += 1;
		if (failed) this.commandsFailed += 1;
		for (const outcome of this.checks) {
			const match = matchCheck(command, outcome.check, this.workspaceDir);
			if (match === "none") continue;
			outcome.ran = true;
			if (match === "exact") outcome.passed = !failed;
			else if (match === "chained") outcome.passed = failed ? null : true;
			else outcome.passed = null;
		}
	}

	view(): RunRecordView {
		return {
			final_text: this.finalText,
			plan: this.plan.map((step) => ({ ...step })),
			files_changed: [...this.files],
			checks: this.checks.map((outcome) => ({ ...outcome })),
			commands: { run: this.commandsRun, failed: this.commandsFailed },
		};
	}
}
