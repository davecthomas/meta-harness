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

export const OWNED_TOOLS = ["submit_review"] as const;

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
