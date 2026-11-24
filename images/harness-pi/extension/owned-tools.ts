/**
 * The meta-harness's own pi tools, which a harness profile names beside pi's built-in tools.
 *
 * `submit_review` ends a meta-harness review run with its verdict. It records
 * the verdict and findings, and the harness server reports them in a
 * `review_submitted` event and in the run's `run_finished` event. It writes
 * nothing anywhere else: the meta-harness run service posts the review on the
 * code host after the run (technical design, principle 9). Its result asks pi
 * to stop, so the review run ends once the verdict is in.
 *
 * `create_issue` lets a planning run propose an issue. It records the draft,
 * which the harness server reports in an `issue_drafted` event, and writes
 * nothing to the code host: the planner creates the issues once an operator
 * approves the plan. A second call with the same draft_id replaces the draft.
 *
 * `plan_issue` and `plan_summary` are the rest of a planning run's tools, and come with `create_issue`. The
 * first records the run's decision on an issue its goal already has (keep it, split it into drafts, or leave it
 * out), reported in an `issue_planned` event. The second records how the plan reaches the goal and what it does
 * not cover, reported in a `plan_summarized` event. Neither writes anywhere else.
 */

import { defineTool, type ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

export const OWNED_TOOLS = ["submit_review", "update_plan", "create_issue", "plan_issue", "plan_summary"] as const;
/** The planning tools a profile gets with create_issue, without naming them. */
export const PLANNING_TOOLS = ["plan_issue", "plan_summary"] as const;

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
	/** False for a follow-up: worth changing, in a later pull request, and no reason to hold this one. */
	blocking: boolean;
}

/** The reviewer's judgment of one finding from the round before, named by the id the prompt gave it. */
export interface Judged {
	id: string;
	fixed: boolean;
}

export interface Review {
	verdict: "clean" | "changes";
	summary: string;
	findings: ReviewFinding[];
	/** Each finding of the round before, judged: fixed or not. Empty on a first review. */
	previous: Judged[];
}

const FindingSchema = Type.Object({
	path: Type.Optional(Type.String({ description: "The file the finding is about, relative to the repository root." })),
	line: Type.Optional(Type.Integer({ minimum: 1, description: "The line in that file, when one line is at fault." })),
	body: Type.String({ minLength: 1, description: "What is wrong and what to change." }),
	blocking: Type.Optional(
		Type.Boolean({
			description:
				"true when the pull request must not merge with this unfixed: the main branch would be worse with it " +
				"than without it. false for a follow-up, which a later pull request makes. Left out, it is true.",
		}),
	),
});

const JudgedSchema = Type.Object({
	id: Type.String({ minLength: 1, description: "The finding's id, as the round before lists it, such as 2.3." }),
	fixed: Type.Boolean({ description: "true when the change you are reviewing fixes it." }),
});

const ReviewSchema = Type.Object({
	verdict: Type.Union([Type.Literal("clean"), Type.Literal("changes")], {
		description: "clean: no finding blocks the merge. changes: at least one finding blocks it.",
	}),
	summary: Type.String({ minLength: 1, description: "The review in a few sentences." }),
	findings: Type.Array(FindingSchema, {
		description: "Each change worth making, blocking or follow-up. A clean review may list follow-ups.",
	}),
	previous: Type.Optional(
		Type.Array(JudgedSchema, {
			description:
				"On a review after the first: each finding of the round before, by its id, and whether it is fixed. " +
				"Left out on a first review.",
		}),
	),
});

/** The submit_review tool. `onReview` receives the review once, when pi calls the tool. */
export function submitReviewTool(onReview: (review: Review) => void): ToolDefinition {
	let submitted = false;
	return defineTool({
		name: "submit_review",
		label: "Submit review",
		description:
			"Submit your review of the pull request and end the review. Call it once, after you have read the " +
			"change and run the checks. Use verdict clean when no finding blocks the merge, and changes " +
			"when at least one finding blocks the merge. Mark each finding blocking or not.",
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
			const findings = params.findings.map((f) => ({
				path: f.path ?? null,
				line: f.line ?? null,
				body: f.body,
				blocking: f.blocking ?? true,
			}));
			if (params.verdict === "changes" && !findings.some((f) => f.blocking)) {
				return {
					content: [
						{
							type: "text",
							text: "A changes verdict needs at least one blocking finding. Call submit_review again: with a finding that blocks, or with verdict clean and the follow-ups.",
						},
					],
					details: undefined,
					isError: true,
				};
			}
			submitted = true;
			const previous = (params.previous ?? []).map((p) => ({ id: p.id.trim(), fixed: p.fixed }));
			onReview({ verdict: params.verdict, summary: params.summary, findings, previous });
			return {
				content: [{ type: "text", text: `Review submitted: ${params.verdict}. Stop now.` }],
				details: undefined,
				terminate: true,
			};
		},
	});
}

export interface IssueDraft {
	/** The agent's own short name for the draft, which other drafts' depends_on may name. */
	draft_id: string;
	repo: string;
	title: string;
	body: string;
	labels: string[];
	/** Other drafts' ids, or existing issues as owner/repo#number. */
	depends_on: string[];
}

const DRAFT_ID = "^[a-z0-9][a-z0-9-]{0,39}$";

const IssueDraftSchema = Type.Object({
	draft_id: Type.String({
		pattern: DRAFT_ID,
		description: "Your own short name for this draft: lowercase letters, digits and hyphens. Other drafts name it in depends_on.",
	}),
	repo: Type.String({ minLength: 1, description: "The repository the issue belongs in, as owner/name." }),
	title: Type.String({ minLength: 1, maxLength: 200, description: "The issue's title." }),
	body: Type.String({
		minLength: 1,
		description: "The issue's body in Markdown. It states what to build and its acceptance checks, under '## Acceptance checks'.",
	}),
	labels: Type.Optional(Type.Array(Type.String({ minLength: 1 }), { description: "Labels for the issue, besides the goal's own." })),
	depends_on: Type.Optional(
		Type.Array(Type.String({ minLength: 1 }), {
			description: "What must be done first: other drafts' draft_id, or existing issues as owner/repo#number.",
		}),
	),
});

/**
 * The create_issue tool, for a planning run. `onDraft` receives each draft as it is recorded. Nothing is
 * created on the code host: a draft is a proposal, which the planner turns into an issue after approval.
 */
export function createIssueTool(onDraft: (draft: IssueDraft) => void): ToolDefinition {
	const drafted = new Set<string>();
	return defineTool({
		name: "create_issue",
		label: "Draft an issue",
		description:
			"Propose one issue for the plan. It is recorded as a draft: nothing is created on the code host until an " +
			"operator approves the plan. Give each draft its own draft_id, state its acceptance checks in the body, " +
			"and name what it depends on. Calling it again with the same draft_id replaces that draft.",
		promptSnippet: "create_issue: propose an issue for the plan, with its acceptance checks and what it depends on",
		parameters: IssueDraftSchema,
		async execute(_toolCallId, params) {
			const draft: IssueDraft = {
				draft_id: params.draft_id,
				repo: params.repo.trim(),
				title: params.title.trim(),
				body: params.body,
				labels: [...new Set(params.labels ?? [])],
				depends_on: [...new Set(params.depends_on ?? [])],
			};
			if (draft.depends_on.includes(draft.draft_id)) {
				return {
					content: [{ type: "text", text: `Draft ${draft.draft_id} cannot depend on itself. Call create_issue again.` }],
					details: undefined,
					isError: true,
				};
			}
			const replaced = drafted.has(draft.draft_id);
			drafted.add(draft.draft_id);
			onDraft(draft);
			return {
				content: [{ type: "text", text: `${replaced ? "Replaced" : "Recorded"} draft ${draft.draft_id}: ${JSON.stringify(draft)}` }],
				details: undefined,
			};
		},
	});
}

export interface IssueDecision {
	/** The existing issue's ref, as the task's description gives it. */
	ref: string;
	decision: "keep" | "split" | "leave_out";
	reason: string;
	/** What a kept issue waits on: other issues' refs, or drafts' ids. */
	depends_on: string[];
	/** For a split: the ids of the drafts that replace the issue. */
	replaced_by: string[];
}

const IssueDecisionSchema = Type.Object({
	ref: Type.String({ minLength: 1, description: "The issue's ref, exactly as the task shows it." }),
	decision: Type.Union([Type.Literal("keep"), Type.Literal("split"), Type.Literal("leave_out")], {
		description: "keep: it runs as it is. split: the drafts in replaced_by run in its place. leave_out: it is not run for this goal.",
	}),
	reason: Type.String({ minLength: 1, description: "Why, in a sentence an operator will read." }),
	depends_on: Type.Optional(
		Type.Array(Type.String({ minLength: 1 }), {
			description: "For keep: what must be done first, as other issues' refs or drafts' draft_id. Only what the work truly needs.",
		}),
	),
	replaced_by: Type.Optional(
		Type.Array(Type.String({ pattern: DRAFT_ID }), { description: "For split: the draft_id of each draft that replaces the issue." }),
	),
});

/**
 * The plan_issue tool, for a planning run. `onDecision` receives each decision as it is recorded. Calling it
 * again for the same issue replaces the decision.
 */
export function planIssueTool(onDecision: (decision: IssueDecision) => void): ToolDefinition {
	const decided = new Set<string>();
	return defineTool({
		name: "plan_issue",
		label: "Decide on an issue",
		description:
			"Record your decision on one issue the goal already has: keep it, split it into drafts you wrote with " +
			"create_issue, or leave it out. Give the reason. Call it once for each issue; calling it again for the same " +
			"issue replaces the decision. Nothing changes until an operator approves the plan.",
		promptSnippet: "plan_issue: decide what the plan does with an existing issue (keep, split, leave out), and why",
		parameters: IssueDecisionSchema,
		async execute(_toolCallId, params) {
			const decision: IssueDecision = {
				ref: params.ref.trim(),
				decision: params.decision,
				reason: params.reason.trim(),
				depends_on: [...new Set(params.depends_on ?? [])],
				replaced_by: [...new Set(params.replaced_by ?? [])],
			};
			const refuse = (text: string) => ({ content: [{ type: "text" as const, text }], details: undefined, isError: true });
			if (decision.decision === "split" && decision.replaced_by.length === 0) {
				return refuse(`A split names the drafts that replace ${decision.ref} in replaced_by. Write them with create_issue, then call plan_issue again.`);
			}
			if (decision.depends_on.includes(decision.ref)) {
				return refuse(`${decision.ref} cannot depend on itself. Call plan_issue again.`);
			}
			const replaced = decided.has(decision.ref);
			decided.add(decision.ref);
			onDecision(decision);
			return {
				content: [{ type: "text", text: `${replaced ? "Replaced" : "Recorded"} the decision on ${decision.ref}: ${decision.decision}` }],
				details: undefined,
			};
		},
	});
}

export interface PlanSummary {
	summary: string;
	gaps: string[];
}

const PlanSummarySchema = Type.Object({
	summary: Type.String({
		minLength: 1,
		description: "How the plan reaches the goal, in a few sentences an operator reads before approving it.",
	}),
	gaps: Type.Optional(
		Type.Array(Type.String({ minLength: 1 }), {
			description: "What the goal asks for that no task covers, any risk you see, and anything you assumed. One per entry.",
		}),
	),
});

/** The plan_summary tool, for a planning run. `onSummary` receives the summary; a later call replaces it. */
export function planSummaryTool(onSummary: (summary: PlanSummary) => void): ToolDefinition {
	return defineTool({
		name: "plan_summary",
		label: "Summarize the plan",
		description:
			"Record how the plan reaches the goal and what it leaves uncovered. Call it once, when the plan is complete. " +
			"Calling it again replaces the summary.",
		promptSnippet: "plan_summary: say how the plan reaches the goal, and its gaps, once the plan is complete",
		parameters: PlanSummarySchema,
		async execute(_toolCallId, params) {
			const summary: PlanSummary = { summary: params.summary.trim(), gaps: [...new Set((params.gaps ?? []).map((g) => g.trim()).filter(Boolean))] };
			onSummary(summary);
			return { content: [{ type: "text", text: "Recorded the plan's summary." }], details: undefined };
		},
	});
}
