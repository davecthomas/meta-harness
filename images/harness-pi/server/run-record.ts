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

/** How one acceptance check ended: `ran` is false when no command matched it, and `passed` is then null. */
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

/** True when the command runs the check: the check, as written, is part of the command. */
export function runsCheck(command: string, check: string, workspaceDir = ""): boolean {
	const wanted = normalizeCommand(check);
	return wanted.length > 0 && normalizeCommand(command, workspaceDir).includes(wanted);
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

	/** One bash command that ended. A later run of a check replaces how an earlier one ended. */
	command(command: string, failed: boolean): void {
		this.commandsRun += 1;
		if (failed) this.commandsFailed += 1;
		for (const outcome of this.checks) {
			if (!runsCheck(command, outcome.check, this.workspaceDir)) continue;
			outcome.ran = true;
			outcome.passed = !failed;
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
