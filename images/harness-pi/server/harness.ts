/**
 * The harness: one pi session in this process, and the runs it serves.
 *
 * A sandbox holds one task's session. `POST /run` creates it and runs the
 * task's prompt; `POST /followup` sends new input to the same session as a new
 * run. Both are idempotent on run_id, so a caller that retries after losing
 * its connection gets the run already in progress and never starts pi twice.
 *
 * Everything pi does arrives through `session.subscribe()` and becomes a typed
 * event in the event log. Nothing is parsed from terminal output.
 */

import { existsSync } from "node:fs";
import { InMemoryCredentialStore, type Model, type Api } from "@earendil-works/pi-ai";
import {
	type AgentSession,
	type AgentSessionEvent,
	createAgentSession,
	DefaultResourceLoader,
	ModelRuntime,
	SessionManager,
	SettingsManager,
} from "@earendil-works/pi-coding-agent";
import {
	createModelClientStream,
	MODEL_CLIENT_API,
	MODEL_CLIENT_MODEL,
	MODEL_CLIENT_PROVIDER,
	type ModelCallRecord,
	type RunContext,
	SPEND_LIMIT_ERROR,
} from "../extension/model-client.ts";
import { bashAllowlist } from "../extension/bash-allowlist.ts";
import {
	createIssueTool,
	type IssueDraft,
	OWNED_TOOLS,
	type PlanStep,
	type Review,
	submitReviewTool,
	updatePlanTool,
} from "../extension/owned-tools.ts";
import { EventLog, type RunEventType } from "./event-log.ts";

/** The Run API's status values, the contract with the harness engine. */
export type RunStatus = "CREATING" | "RUNNING" | "FINISHED" | "ERROR" | "CANCELED" | "EXPIRED";

/** pi's built-in tools a profile may name. The meta-harness's own tools are in OWNED_TOOLS. */
export const BUILT_IN_TOOLS = ["read", "write", "edit", "ls", "find", "grep", "bash"] as const;

export interface Profile {
	name: string;
	tools: string[];
	max_turns: number;
	max_minutes: number;
	/** The programs pi's bash tool may run. Absent: any program. */
	bash_allow?: string[];
}

/** The thinking levels the HTTP Gateway's conversation turn accepts. */
export const THINKING_LEVELS = ["off", "minimal", "low", "medium", "high", "max"] as const;
export type ThinkingLevel = (typeof THINKING_LEVELS)[number];

export interface RunRequest {
	run_id: string;
	harness_session_id: string;
	prompt: string;
	acceptance_checks: string[];
	profile: Profile;
	/** The routing tier's thinking level, sent on every turn. Absent: the gateway's default. */
	thinking?: ThinkingLevel;
	/** The run grant's key from the HTTP Gateway. The grant decides the engine, the model and the spend limit. */
	grant_key: string;
}

export interface FollowupRequest {
	run_id: string;
	prompt: string;
	/** A follow-up is a new run with its own grant. */
	grant_key: string;
}

export interface Usage {
	input_tokens: number;
	output_tokens: number;
	cached_input_tokens: number;
	cache_write_5m_tokens: number;
	cache_write_1h_tokens: number;
	calls: number;
	failed_calls: number;
	/** The HTTP Gateway's running total for the run: the sum of each turn's `usd_cost`. */
	usd: number;
}

export interface RunState {
	run_id: string;
	kind: "run" | "followup";
	harness_session_id: string;
	profile: string;
	acceptance_checks: string[];
	status: RunStatus;
	/** Why the run ended: completed, model_error, harness_error, canceled, max_turns, max_minutes, spend_limit, server_shutdown. */
	end_reason: string | null;
	error: string | null;
	turns: number;
	/** A review run's verdict and findings, from submit_review. Null until it is called. */
	review: Review | null;
	started_at: string;
	ended_at: string | null;
	last_heartbeat_at: string | null;
	usage: Usage;
}

export interface HarnessConfig {
	gatewayUrl: string;
	workspaceDir: string;
	sessionDir: string;
	agentDir: string;
	allowBash: boolean;
	heartbeatSeconds: number;
	log: (text: string) => void;
}

export class HttpError extends Error {
	readonly status: number;
	constructor(status: number, message: string) {
		super(message);
		this.status = status;
	}
}

type StopReason = "canceled" | "max_turns" | "max_minutes" | "spend_limit" | "server_shutdown" | "operator_stop";

const ACTIVE: readonly RunStatus[] = ["CREATING", "RUNNING"];
const PREVIEW_CHARS = 2000;

function preview(value: unknown): unknown {
	const text = typeof value === "string" ? value : JSON.stringify(value);
	if (text === undefined || text.length <= PREVIEW_CHARS) return value;
	return { truncated: true, chars: text.length, preview: text.slice(0, PREVIEW_CHARS) };
}

function emptyUsage(): Usage {
	return {
		input_tokens: 0,
		output_tokens: 0,
		cached_input_tokens: 0,
		cache_write_5m_tokens: 0,
		cache_write_1h_tokens: 0,
		calls: 0,
		failed_calls: 0,
		usd: 0,
	};
}

export class Harness {
	readonly events = new EventLog();
	private readonly config: HarnessConfig;
	private readonly modelRuntime: ModelRuntime;
	private readonly model: Model<Api>;
	private readonly runs = new Map<string, RunState>();
	private readonly finished = new Map<string, Promise<void>>();
	private session?: AgentSession;
	private sessionProfile?: Profile;
	private current?: RunState;
	private context?: RunContext;
	private stopReason?: StopReason;
	// An operator asked to stop once the turn in progress ends.
	private stopAfterThisTurn = false;
	private lastAssistantError?: string;
	// Args of tool calls still running, by tool call id. A list, because Gemini's tool calls carry no id
	// and the library sets each id to the tool's name, so two parallel writes share one.
	private readonly toolArgs = new Map<string, unknown[]>();

	private constructor(config: HarnessConfig, modelRuntime: ModelRuntime, model: Model<Api>) {
		this.config = config;
		this.modelRuntime = modelRuntime;
		this.model = model;
	}

	/** Builds pi's model layer with the model client as its only provider. */
	static async create(config: HarnessConfig): Promise<Harness> {
		const modelRuntime = await ModelRuntime.create({
			credentials: new InMemoryCredentialStore(),
			modelsPath: null,
			allowModelNetwork: false,
		});
		let harness: Harness | undefined;
		modelRuntime.registerProvider(MODEL_CLIENT_PROVIDER, {
			name: "ai-api-unified through the HTTP Gateway",
			baseUrl: config.gatewayUrl,
			// A fixed placeholder so pi treats the provider as configured. The real grant key
			// comes from the run on every turn. A key here would also be at risk: pi runs a value
			// that starts with "!" as a shell command.
			apiKey: "run-grant-key",
			api: MODEL_CLIENT_API,
			models: [
				{
					id: MODEL_CLIENT_MODEL,
					name: "The run's model",
					reasoning: false,
					input: ["text"],
					cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
					contextWindow: 200000,
					maxTokens: 8192,
				},
			],
			streamSimple: createModelClientStream({
				gatewayUrl: config.gatewayUrl,
				currentRun: () => {
					if (!harness?.context) throw new Error("A model call arrived with no run in progress.");
					return harness.context;
				},
				onModelCall: (call) => harness?.recordModelCall(call),
			}),
		});
		const reachable = await modelRuntime.getAvailable();
		if (reachable.length !== 1 || reachable[0]!.provider !== MODEL_CLIENT_PROVIDER) {
			const names = reachable.map((m) => `${m.provider}/${m.id}`).join(", ");
			throw new Error(`pi can reach models other than the model client's: ${names}. Remove provider keys from the environment.`);
		}
		const model = modelRuntime.getModel(MODEL_CLIENT_PROVIDER, MODEL_CLIENT_MODEL);
		if (!model) throw new Error("The model client's model is not registered.");
		harness = new Harness(config, modelRuntime, model);
		return harness;
	}

	get sessionId(): string | undefined {
		return this.session?.sessionId;
	}

	get workspaceDir(): string {
		return this.config.workspaceDir;
	}

	/** The run with this id, or the latest run when no id is given. */
	getRun(runId?: string | null): RunState | undefined {
		return runId ? this.runs.get(runId) : this.current;
	}

	async health(): Promise<{ ok: boolean; checks: Record<string, boolean>; session_id: string | null }> {
		let gateway = false;
		try {
			const response = await fetch(`${this.config.gatewayUrl}/healthz`, { signal: AbortSignal.timeout(2000) });
			gateway = response.ok;
		} catch {
			gateway = false;
		}
		const checks = {
			model_client_registered: true,
			workspace_exists: existsSync(this.config.workspaceDir),
			gateway_reachable: gateway,
		};
		return { ok: Object.values(checks).every(Boolean), checks, session_id: this.sessionId ?? null };
	}

	/** POST /run. Returns created false when this run_id already exists. */
	start(request: RunRequest): { created: boolean; run: RunState } {
		const existing = this.runs.get(request.run_id);
		if (existing) return { created: false, run: existing };
		if (this.session || this.current) {
			throw new HttpError(409, `This sandbox already holds session ${this.current?.harness_session_id}. Use POST /followup.`);
		}
		for (const tool of request.profile.tools) {
			const known = [...BUILT_IN_TOOLS, ...OWNED_TOOLS] as readonly string[];
			if (!known.includes(tool)) throw new HttpError(400, `Unknown tool "${tool}".`);
			if (tool === "bash" && !this.config.allowBash) {
				throw new HttpError(400, "The bash tool is off. Set HARNESS_ALLOW_BASH=1 only inside a sandbox.");
			}
		}
		const run = this.newRun(request.run_id, "run", request.harness_session_id, request.profile, request.acceptance_checks);
		this.sessionProfile = request.profile;
		this.context = { grantKey: request.grant_key, cacheKey: request.harness_session_id, thinking: request.thinking };
		this.track(run, async () => {
			this.session = await this.createSession(request.harness_session_id, request.profile);
			await this.execute(run, request.prompt);
		});
		return { created: true, run };
	}

	/** POST /followup: new input to the session this sandbox holds. */
	followup(request: FollowupRequest): { created: boolean; run: RunState } {
		const existing = this.runs.get(request.run_id);
		if (existing) return { created: false, run: existing };
		if (!this.session || !this.current || !this.sessionProfile) {
			throw new HttpError(409, "This sandbox holds no session. Restoring a saved session is not built yet.");
		}
		if (ACTIVE.includes(this.current.status)) {
			throw new HttpError(409, `Run ${this.current.run_id} is still ${this.current.status}.`);
		}
		const run = this.newRun(request.run_id, "followup", this.current.harness_session_id, this.sessionProfile, []);
		// A follow-up keeps the session's thinking level.
		this.context = { grantKey: request.grant_key, cacheKey: run.harness_session_id, thinking: this.context?.thinking };
		this.track(run, () => this.execute(run, request.prompt));
		return { created: true, run };
	}

	/**
	 * POST /messages: an operator's message to the run in progress. next_turn steers pi, so it reads the
	 * message at its next turn; after_stop queues it as a follow-up for when pi would otherwise stop.
	 */
	async message(text: string, when: "next_turn" | "after_stop"): Promise<RunState> {
		const run = this.current;
		if (!run || !ACTIVE.includes(run.status) || !this.session) throw new HttpError(409, "No run in progress to message.");
		if (when === "after_stop") await this.session.followUp(text);
		else await this.session.steer(text);
		this.emit("operator_message_delivered", run, { when, chars: text.length });
		return run;
	}

	/** POST /stop: ends the run at the end of the turn in progress, keeping its work. */
	stopAfterTurn(): RunState {
		const run = this.current;
		if (!run || !ACTIVE.includes(run.status)) throw new HttpError(409, "No run in progress to stop.");
		this.stopAfterThisTurn = true;
		return run;
	}

	/** POST /cancel: stops pi and returns once the run has ended. */
	async cancel(): Promise<RunState | undefined> {
		const run = this.current;
		if (!run) return undefined;
		if (ACTIVE.includes(run.status)) await this.stop("canceled");
		await this.finished.get(run.run_id);
		return run;
	}

	/** Stops the run in progress before the process exits. */
	async shutdown(): Promise<void> {
		const run = this.current;
		if (run && ACTIVE.includes(run.status)) {
			await this.stop("server_shutdown");
			await Promise.race([this.finished.get(run.run_id), new Promise((resolve) => setTimeout(resolve, 5000))]);
		}
		this.session?.dispose();
	}

	private newRun(
		runId: string,
		kind: RunState["kind"],
		sessionId: string,
		profile: Profile,
		acceptanceChecks: string[],
	): RunState {
		const run: RunState = {
			run_id: runId,
			kind,
			harness_session_id: sessionId,
			profile: profile.name,
			acceptance_checks: acceptanceChecks,
			status: "CREATING",
			end_reason: null,
			error: null,
			turns: 0,
			review: null,
			started_at: new Date().toISOString(),
			ended_at: null,
			last_heartbeat_at: null,
			usage: emptyUsage(),
		};
		this.runs.set(runId, run);
		this.current = run;
		this.config.log(`run ${runId} (${kind}) accepted, session ${sessionId}, profile ${profile.name}`);
		return run;
	}

	/** Runs `body` in the background and records how the run ended if it throws. */
	private track(run: RunState, body: () => Promise<void>): void {
		const done = body().catch((error: unknown) => {
			if (run.ended_at) return;
			this.end(run, "ERROR", "harness_error", error instanceof Error ? error.message : String(error));
		});
		this.finished.set(run.run_id, done);
	}

	private async createSession(sessionId: string, profile: Profile): Promise<AgentSession> {
		const settingsManager = SettingsManager.inMemory({
			// pi's retry would stack on the gateway's fallback chain: a failed turn fails the run.
			retry: { enabled: false },
			cacheWarming: "off",
		});
		const resourceLoader = new DefaultResourceLoader({
			cwd: this.config.workspaceDir,
			agentDir: this.config.agentDir,
			settingsManager,
			noExtensions: true,
			noSkills: true,
			noPromptTemplates: true,
			noThemes: true,
			noContextFiles: true,
			// Inline factories load even with noExtensions: they are the server's own code, not files from the repo.
			extensionFactories: profile.bash_allow
				? [{ name: "bash-allowlist", factory: bashAllowlist(profile.bash_allow) }]
				: [],
		});
		await resourceLoader.reload();
		const { session } = await createAgentSession({
			cwd: this.config.workspaceDir,
			agentDir: this.config.agentDir,
			model: this.model,
			thinkingLevel: "off",
			modelRuntime: this.modelRuntime,
			settingsManager,
			resourceLoader,
			// The session file goes outside the workspace, so it never lands in the repo or the PR.
			sessionManager: SessionManager.create(this.config.workspaceDir, this.config.sessionDir, { id: sessionId }),
			// A run that edits keeps a plan too: update_plan comes with write, without the profile naming it, since a
			// deploy never rewrites the deployment's active profiles.
			tools: profile.tools.includes("write") && !profile.tools.includes("update_plan") ? [...profile.tools, "update_plan"] : profile.tools,
			customTools: [
				...(profile.tools.includes("submit_review") ? [submitReviewTool((review) => this.onReview(review))] : []),
				...(profile.tools.includes("write") || profile.tools.includes("update_plan") ? [updatePlanTool((steps) => this.onPlan(steps))] : []),
				...(profile.tools.includes("create_issue") ? [createIssueTool((draft) => this.onDraft(draft))] : []),
			],
		});
		session.subscribe((event) => this.onPiEvent(event));
		this.config.log(`session ${session.sessionId} created, tools ${session.getActiveToolNames().join(", ")}`);
		return session;
	}

	private async execute(run: RunState, prompt: string): Promise<void> {
		const session = this.session!;
		const profile = this.sessionProfile!;
		this.stopReason = undefined;
		this.stopAfterThisTurn = false;
		this.lastAssistantError = undefined;
		run.status = "RUNNING";
		const startedAt = Date.now();
		const heartbeat = setInterval(() => {
			run.last_heartbeat_at = new Date().toISOString();
			this.emit("heartbeat", run, { turns: run.turns, elapsed_ms: Date.now() - startedAt });
		}, this.config.heartbeatSeconds * 1000);
		const deadline = setTimeout(() => void this.stop("max_minutes"), profile.max_minutes * 60_000);
		let thrown: string | undefined;
		try {
			await session.prompt(prompt, { expandPromptTemplates: false });
		} catch (error) {
			thrown = error instanceof Error ? error.message : String(error);
		} finally {
			clearInterval(heartbeat);
			clearTimeout(deadline);
		}
		// stop() sets stopReason while prompt() is pending, which TypeScript's narrowing cannot see.
		const stopped = this.stopReason as StopReason | undefined;
		switch (stopped) {
			case "canceled":
				return this.end(run, "CANCELED", "canceled", null);
			case "max_minutes":
				return this.end(run, "EXPIRED", "max_minutes", `The run passed its ${profile.max_minutes} minute limit.`);
			case "max_turns":
				return this.end(run, "ERROR", "max_turns", `The run passed its ${profile.max_turns} turn limit.`);
			case "spend_limit":
				return this.end(run, "ERROR", "spend_limit", this.lastAssistantError ?? "The run reached its grant's spend limit.");
			case "server_shutdown":
				return this.end(run, "ERROR", "server_shutdown", "The harness server stopped during the run.");
			case "operator_stop":
				// Stopped after a turn on an operator's word: the work so far is the run's work, and it publishes.
				return this.end(run, "FINISHED", "stopped_by_operator", null);
		}
		if (thrown) return this.end(run, "ERROR", "harness_error", thrown);
		if (this.lastAssistantError) return this.end(run, "ERROR", "model_error", this.lastAssistantError);
		return this.end(run, "FINISHED", "completed", null);
	}

	private end(run: RunState, status: RunStatus, endReason: string, error: string | null): void {
		run.status = status;
		run.end_reason = endReason;
		run.error = error;
		run.ended_at = new Date().toISOString();
		const elapsedMs = Date.parse(run.ended_at) - Date.parse(run.started_at);
		this.emit("run_finished", run, {
			status,
			end_reason: endReason,
			error,
			turns: run.turns,
			elapsed_ms: elapsedMs,
			usage: run.usage,
			review: run.review,
		});
		this.config.log(`run ${run.run_id} ${status} (${endReason})${error ? `: ${error}` : ""}`);
	}

	private async stop(reason: StopReason): Promise<void> {
		const run = this.current;
		if (!run || !ACTIVE.includes(run.status) || this.stopReason) return;
		this.stopReason = reason;
		this.config.log(`run ${run.run_id}: stopping pi (${reason})`);
		await this.session?.abort();
	}

	/** update_plan's checklist, for the run in progress. */
	private onPlan(steps: PlanStep[]): void {
		const run = this.current;
		if (!run) return;
		this.emit("plan_updated", run, { steps });
	}

	/** One draft from create_issue, for the run in progress. */
	private onDraft(draft: IssueDraft): void {
		const run = this.current;
		if (!run) return;
		this.emit("issue_drafted", run, { ...draft });
		this.config.log(`run ${run.run_id}: issue drafted, ${draft.draft_id} in ${draft.repo}`);
	}

	/** submit_review's verdict, for the run in progress. */
	private onReview(review: Review): void {
		const run = this.current;
		if (!run) return;
		run.review = review;
		this.emit("review_submitted", run, { ...review });
		this.config.log(`run ${run.run_id}: review submitted, verdict ${review.verdict}, ${review.findings.length} findings`);
	}

	private emit(type: RunEventType, run: RunState, data: Record<string, unknown>): void {
		this.events.append(type, run.run_id, data);
	}

	/** Called by the model client after every gateway call, before pi sees the result. */
	private recordModelCall(call: ModelCallRecord): void {
		const run = this.current;
		if (!run) return;
		const usage = run.usage;
		usage.calls += 1;
		const response = call.response;
		if (!response) {
			usage.failed_calls += 1;
			this.emit("model_call", run, {
				turn: run.turns,
				latency_ms: call.latencyMs,
				error: call.error,
				error_code: call.errorCode ?? null,
				aborted: call.aborted ?? false,
			});
			// The grant refuses every further turn, so the run ends now, with its own reason.
			if (call.errorCode === SPEND_LIMIT_ERROR) void this.stop("spend_limit");
			return;
		}
		const u = response.usage;
		usage.input_tokens += u.input_tokens;
		usage.output_tokens += u.output_tokens;
		usage.cached_input_tokens += u.cached_input_tokens ?? 0;
		usage.cache_write_5m_tokens += u.cache_write_5m_tokens ?? 0;
		usage.cache_write_1h_tokens += u.cache_write_1h_tokens ?? 0;
		const usd = response.usd_cost === null ? 0 : Number(response.usd_cost);
		usage.usd = Number((usage.usd + usd).toFixed(6));
		this.emit("model_call", run, {
			turn: run.turns,
			engine: response.engine,
			model: response.model,
			served_by_fallback: response.served_by_fallback,
			finish_reason: response.finish_reason,
			tool_calls: response.tool_calls.map((c) => c.name),
			input_tokens: u.input_tokens,
			output_tokens: u.output_tokens,
			cached_input_tokens: u.cached_input_tokens ?? 0,
			cache_write_5m_tokens: u.cache_write_5m_tokens ?? 0,
			cache_write_1h_tokens: u.cache_write_1h_tokens ?? 0,
			usd,
			run_spent_usd: response.run_grant?.spent_usd ?? null,
			run_spend_limit_usd: response.run_grant?.spend_limit_usd ?? null,
			cache_hit: response.cache_hit,
			latency_ms: call.latencyMs,
		});
	}

	private onPiEvent(event: AgentSessionEvent): void {
		const run = this.current;
		if (!run) return;
		switch (event.type) {
			case "turn_start": {
				run.turns += 1;
				if (run.turns > this.sessionProfile!.max_turns) {
					void this.stop("max_turns");
					return;
				}
				this.emit("turn_started", run, { turn: run.turns });
				return;
			}
			case "message_end": {
				const message = event.message;
				if (message.role === "assistant" && message.stopReason === "error") {
					this.lastAssistantError = message.errorMessage ?? "The model call failed.";
				}
				return;
			}
			case "turn_end": {
				const message = event.message;
				if (message.role !== "assistant") return;
				const text = message.content
					.filter((block) => block.type === "text")
					.map((block) => block.text)
					.join("");
				this.emit("turn_finished", run, {
					turn: run.turns,
					stop_reason: message.stopReason,
					text: preview(text),
					tool_calls: message.content.filter((block) => block.type === "toolCall").length,
				});
				if (this.stopAfterThisTurn) void this.stop("operator_stop");
				return;
			}
			case "tool_execution_start": {
				this.toolArgs.set(event.toolCallId, [...(this.toolArgs.get(event.toolCallId) ?? []), event.args]);
				this.emit("tool_call", run, { tool_call_id: event.toolCallId, tool: event.toolName, args: preview(event.args) });
				return;
			}
			case "tool_execution_end": {
				const pending = this.toolArgs.get(event.toolCallId) ?? [];
				const args = pending.shift() as { path?: unknown } | undefined;
				if (pending.length === 0) this.toolArgs.delete(event.toolCallId);
				const output = event.result?.content?.[0]?.text ?? event.result;
				this.emit("tool_result", run, {
					tool_call_id: event.toolCallId,
					tool: event.toolName,
					is_error: event.isError,
					output: preview(output),
					duration_ms: event.durationMs ?? null,
				});
				if ((event.toolName === "write" || event.toolName === "edit") && !event.isError && typeof args?.path === "string") {
					this.emit("file_changed", run, { path: args.path, tool: event.toolName });
				}
				return;
			}
			case "compaction_end": {
				this.emit("compacted", run, { reason: event.reason });
				return;
			}
		}
	}
}
