/**
 * The model client: the only provider pi can see.
 *
 * pi calls `streamSimple` once per model turn. The model client sends the turn
 * to the HTTP Gateway's `POST /v1/conversations/turn`, where ai-api-unified
 * picks and calls the real model, and maps the answer back into pi's events.
 *
 * Contract followed here (pi docs/custom-provider.md, "Implement custom
 * streaming"): one `start`, balanced content events, exactly one `done` or
 * `error`, accurate usage and cost, the onPayload / onResponse /
 * onProviderStreamEvent hooks, and an aborted result on cancel.
 */

import {
	type Api,
	type AssistantMessage,
	type AssistantMessageEventStream,
	collapseSystemMessages,
	createAssistantMessageEventStream,
	getCurrentSystemPrompt,
	getCurrentTools,
	type Model,
	type SimpleStreamOptions,
	type StopReason,
	type ToolCall,
	type TranscriptContext,
} from "@earendil-works/pi-ai";
import { toGatewayMessages, type GatewayMessage } from "./gateway-messages.ts";

export const MODEL_CLIENT_PROVIDER = "ai-api-unified";
export const MODEL_CLIENT_MODEL = "run-model";
export const MODEL_CLIENT_API = "ai-api-unified-conversation-turn";

/** What the HTTP Gateway returns from `/v1/conversations/turn` (ai-api-unified-http 1.17.0). */
interface GatewayTurnResponse {
	text: string | null;
	tool_calls: { id: string; name: string; input: Record<string, unknown> }[];
	finish_reason: "complete" | "length" | "tool_use" | "refusal";
	usage: {
		input_tokens: number;
		output_tokens: number;
		cached_input_tokens: number | null;
		cache_write_5m_tokens: number | null;
		cache_write_1h_tokens: number | null;
		total_tokens: number;
	};
	usd_cost: string | null;
	cache_hit: boolean;
	conversation_token: string | null;
	engine: string;
	model: string | null;
	served_by_fallback: boolean;
}

/** One model turn as the trace prints it. */
export interface ModelClientTurn {
	requestedEngine: string;
	requestedModel: string | undefined;
	messageCount: number;
	toolNames: string[];
	response?: GatewayTurnResponse;
	error?: string;
}

export interface ModelClientOptions {
	gatewayUrl: string;
	/** Engine and model the request names. In the full design the run token decides these. */
	engine: string;
	model?: string;
	/** `prompt_cache.key`: the harness session id, so every turn of a session shares one cache. */
	cacheKey: string;
	onTurn?: (turn: ModelClientTurn) => void;
}

/**
 * Stores which engine answered a turn next to its replay token. pi keeps
 * `responseId` on the assistant message, in memory and in the session file,
 * and hands it back on the next turn. The gateway needs both: the token to
 * replay the turn, and the engine to shape the tool results that follow it.
 */
export function encodeResponseId(engine: string, token: string): string {
	return `${engine}:${token}`;
}

export function decodeResponseId(responseId: string | undefined): { engine: string; token: string } | undefined {
	if (!responseId) return undefined;
	const colon = responseId.indexOf(":");
	if (colon <= 0) return undefined;
	return { engine: responseId.slice(0, colon), token: responseId.slice(colon + 1) };
}

function mapStopReason(finish: GatewayTurnResponse["finish_reason"]): StopReason {
	switch (finish) {
		case "tool_use":
			return "toolUse";
		case "length":
			return "length";
		default:
			return "stop";
	}
}

export function createModelClientStream(clientOptions: ModelClientOptions) {
	return function streamSimple(
		model: Model<Api>,
		context: TranscriptContext,
		options?: SimpleStreamOptions,
	): AssistantMessageEventStream {
		const stream = createAssistantMessageEventStream();
		const transcript = collapseSystemMessages(context);
		const systemPrompt = getCurrentSystemPrompt(transcript.messages);
		const tools = getCurrentTools(transcript.messages);

		const output: AssistantMessage = {
			role: "assistant",
			content: [],
			api: model.api,
			provider: model.provider,
			model: model.id,
			usage: {
				input: 0,
				output: 0,
				cacheRead: 0,
				cacheWrite: 0,
				totalTokens: 0,
				cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
			},
			stopReason: "pending",
			timestamp: Date.now(),
		};

		const turn: ModelClientTurn = {
			requestedEngine: clientOptions.engine,
			requestedModel: clientOptions.model,
			messageCount: 0,
			toolNames: tools.map((tool) => tool.name),
		};

		(async () => {
			try {
				const messages: GatewayMessage[] = toGatewayMessages(transcript.messages);
				turn.messageCount = messages.length;
				let body: unknown = {
					engine: clientOptions.engine,
					model: clientOptions.model,
					system_prompt: systemPrompt,
					messages,
					tools: tools.map((tool) => ({
						name: tool.name,
						description: tool.description,
						// pi tool parameters are JSON Schema; JSON encoding drops TypeBox's symbol keys.
						input_schema: JSON.parse(JSON.stringify(tool.parameters)),
					})),
					max_response_tokens: options?.maxTokens ?? model.maxTokens,
					prompt_cache: { retention: "extended", key: clientOptions.cacheKey },
					// pi's own retry is off; the gateway's fallback chain handles provider failures.
					fallback: true,
				};
				const replaced = await options?.onPayload?.(body, model);
				if (replaced !== undefined) body = replaced;

				const headers: Record<string, string> = { "content-type": "application/json" };
				if (options?.apiKey) headers.authorization = `Bearer ${options.apiKey}`;
				const response = await fetch(`${clientOptions.gatewayUrl}/v1/conversations/turn`, {
					method: "POST",
					headers,
					body: JSON.stringify(body),
					signal: options?.signal,
				});
				await options?.onResponse?.(
					{ status: response.status, headers: Object.fromEntries(response.headers.entries()) },
					model,
				);
				const json = (await response.json()) as unknown;
				if (!response.ok) {
					const detail = (json as { detail?: unknown; error?: unknown }).detail ?? (json as { error?: unknown }).error;
					throw new Error(`HTTP Gateway answered ${response.status}: ${JSON.stringify(detail ?? json)}`);
				}
				// The gateway answers each turn with one JSON body, so this is the turn's only provider event.
				await options?.onProviderStreamEvent?.(json, model);
				const result = json as GatewayTurnResponse;
				turn.response = result;

				stream.push({ type: "start", partial: output });

				if (result.text) {
					output.content.push({ type: "text", text: "" });
					const index = output.content.length - 1;
					stream.push({ type: "text_start", contentIndex: index, partial: output });
					(output.content[index] as { text: string }).text = result.text;
					stream.push({ type: "text_delta", contentIndex: index, delta: result.text, partial: output });
					stream.push({ type: "text_end", contentIndex: index, content: result.text, partial: output });
				}

				for (const call of result.tool_calls) {
					const toolCall: ToolCall = { type: "toolCall", id: call.id, name: call.name, arguments: {} };
					output.content.push(toolCall);
					const index = output.content.length - 1;
					stream.push({ type: "toolcall_start", contentIndex: index, partial: output });
					const argumentsText = JSON.stringify(call.input ?? {});
					toolCall.arguments = (call.input ?? {}) as ToolCall["arguments"];
					stream.push({ type: "toolcall_delta", contentIndex: index, delta: argumentsText, partial: output });
					stream.push({ type: "toolcall_end", contentIndex: index, toolCall, partial: output });
				}

				const usage = result.usage;
				const cacheRead = usage.cached_input_tokens ?? 0;
				const cacheWrite5m = usage.cache_write_5m_tokens ?? 0;
				const cacheWrite1h = usage.cache_write_1h_tokens ?? 0;
				output.usage.input = usage.input_tokens;
				output.usage.output = usage.output_tokens;
				output.usage.cacheRead = cacheRead;
				output.usage.cacheWrite = cacheWrite5m + cacheWrite1h;
				if (cacheWrite1h > 0) output.usage.cacheWrite1h = cacheWrite1h;
				output.usage.totalTokens = usage.total_tokens;
				// The model entry carries zero prices: the gateway prices the turn at the model that answered.
				const usd = result.usd_cost === null ? 0 : Number(result.usd_cost);
				output.usage.cost = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: usd, totalFromProvider: true };
				output.responseModel = result.model ?? undefined;
				if (result.conversation_token) {
					output.responseId = encodeResponseId(result.engine, result.conversation_token);
				}
				output.stopReason = mapStopReason(result.finish_reason);
				if (output.stopReason === "toolUse" && result.tool_calls.length === 0) output.stopReason = "stop";

				clientOptions.onTurn?.(turn);
				stream.push({ type: "done", reason: output.stopReason as "stop" | "length" | "toolUse", message: output });
				stream.end();
			} catch (error) {
				output.stopReason = options?.signal?.aborted ? "aborted" : "error";
				output.errorMessage = error instanceof Error ? error.message : String(error);
				turn.error = output.errorMessage;
				clientOptions.onTurn?.(turn);
				stream.push({ type: "error", reason: output.stopReason, error: output });
				stream.end();
			}
		})();

		return stream;
	};
}
