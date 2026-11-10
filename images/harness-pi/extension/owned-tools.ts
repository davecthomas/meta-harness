/**
 * The meta-harness's own pi tools, which a harness profile names beside pi's built-in tools.
 *
 * `submit_review` ends a meta-harness review run with its verdict. It records
 * the verdict and findings, and the harness server reports them in a
 * `review_submitted` event and in the run's `run_finished` event. It writes
 * nothing anywhere else: the meta-harness run service posts the review on the
 * code host after the run (technical design, principle 9). Its result asks pi
 * to stop, so the review run ends once the verdict is in.
 */

import { defineTool, type ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

export const OWNED_TOOLS = ["submit_review", "update_plan"] as const;

export interface PlanStep {
	step: string;
	status: "pending" | "in_progress" | "done";
}

const PlanSchema = Type.Object({
	steps: Type.Array(
		Type.Object({
			step: Type.String({ minLength: 1, description: "One step, in a few words." }),
			status: Type.Union([Type.Literal("pending"), Type.Literal("in_progress"), Type.Literal("done")]),
		}),
		{ minItems: 1, description: "The whole plan, in order. Each call replaces the last." },
	),
});

/**
 * The update_plan tool: the agent keeps its plan as a checklist, the whole plan on each call. The
 * harness server reports it in a `plan_updated` event, which the console shows as the task's plan.
 * It changes nothing in the workspace.
 */
export function updatePlanTool(onPlan: (steps: PlanStep[]) => void): ToolDefinition {
	return defineTool({
		name: "update_plan",
		label: "Update plan",
		description:
			"Record your plan as a checklist: the whole plan, every step with its status (pending, in_progress or " +
			"done). Call it when you make the plan and whenever a step starts or finishes.",
		promptSnippet: "update_plan: keep your plan as a checklist the operator can follow",
		parameters: PlanSchema,
		async execute(_toolCallId, params) {
			const steps = params.steps.map((s) => ({ step: s.step, status: s.status }));
			onPlan(steps);
			const done = steps.filter((s) => s.status === "done").length;
			return { content: [{ type: "text", text: `Plan recorded: ${done} of ${steps.length} steps done.` }], details: undefined };
		},
	});
}

export interface ReviewFinding {
	path: string | null;
	line: number | null;
	body: string;
}

export interface Review {
	verdict: "clean" | "changes";
	summary: string;
	findings: ReviewFinding[];
}

const FindingSchema = Type.Object({
	path: Type.Optional(Type.String({ description: "The file the finding is about, relative to the repository root." })),
	line: Type.Optional(Type.Integer({ minimum: 1, description: "The line in that file, when one line is at fault." })),
	body: Type.String({ minLength: 1, description: "What is wrong and what to change." }),
});

const ReviewSchema = Type.Object({
	verdict: Type.Union([Type.Literal("clean"), Type.Literal("changes")], {
		description: "clean: the pull request may merge as it is. changes: it must change first.",
	}),
	summary: Type.String({ minLength: 1, description: "The review in a few sentences." }),
	findings: Type.Array(FindingSchema, { description: "Each change the pull request needs. Empty for clean." }),
});

/** The submit_review tool. `onReview` receives the review once, when pi calls the tool. */
export function submitReviewTool(onReview: (review: Review) => void): ToolDefinition {
	let submitted = false;
	return defineTool({
		name: "submit_review",
		label: "Submit review",
		description:
			"Submit your review of the pull request and end the review. Call it once, after you have read the " +
			"change and run the checks. Use verdict clean when the pull request may merge as it is, and changes " +
			"when it must change first, with one finding per needed change.",
		promptSnippet: "submit_review: submit the review's verdict and findings, and end the review",
		parameters: ReviewSchema,
		async execute(_toolCallId, params) {
			if (submitted) {
				return {
					content: [{ type: "text", text: "The review is already submitted. Stop now." }],
					details: undefined,
					isError: true,
					terminate: true,
				};
			}
			const findings = params.findings.map((f) => ({ path: f.path ?? null, line: f.line ?? null, body: f.body }));
			if (params.verdict === "changes" && findings.length === 0) {
				return {
					content: [{ type: "text", text: "A changes verdict needs at least one finding. Call submit_review again." }],
					details: undefined,
					isError: true,
				};
			}
			submitted = true;
			onReview({ verdict: params.verdict, summary: params.summary, findings });
			return {
				content: [{ type: "text", text: `Review submitted: ${params.verdict}. Stop now.` }],
				details: undefined,
				terminate: true,
			};
		},
	});
}
