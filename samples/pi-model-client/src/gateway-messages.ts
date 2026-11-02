/**
 * Converts pi's transcript into the message list `/v1/conversations/turn` takes.
 *
 * - User text goes as `{role: "user", content}`, which every engine accepts.
 * - An assistant turn goes back as its `conversation_token`, which the HTTP
 *   Gateway expands into the answering engine's own shape.
 * - A tool result has to be in the shape of the engine that asked for the
 *   tool. The gateway takes no engine-neutral tool result, so the shapes below
 *   copy ai-api-unified's `build_tool_result_message` for each engine
 *   (library 3.28.0). This duplication is a gap in the gateway, recorded in
 *   the README.
 */

import type { Message, ToolResultMessage } from "@earendil-works/pi-ai";
import { decodeResponseId } from "./model-client.ts";

export type GatewayMessage = Record<string, unknown>;

function textOf(content: string | readonly { type: string; text?: string }[]): string {
	if (typeof content === "string") return content;
	return content
		.map((block) => (block.type === "text" ? (block.text ?? "") : `[${block.type} omitted]`))
		.join("\n");
}

/** Mirrors each engine's `_build_tool_result_message_provider` in ai-api-unified. */
function toolResultMessage(engine: string, result: ToolResultMessage): GatewayMessage {
	const payload: Record<string, unknown> = { output: textOf(result.content) };
	switch (engine) {
		case "claude":
		case "anthropic":
			return {
				role: "user",
				content: [
					{
						type: "tool_result",
						tool_use_id: result.toolCallId,
						content: JSON.stringify(payload),
						is_error: result.isError,
					},
				],
			};
		case "openai":
			return {
				role: "tool",
				tool_call_id: result.toolCallId,
				content: JSON.stringify(result.isError ? { error: payload } : payload),
			};
		case "openai-responses":
			return {
				type: "function_call_output",
				call_id: result.toolCallId,
				output: JSON.stringify(result.isError ? { error: payload } : payload),
			};
		case "google-gemini":
			// Gemini calls carry no id; the library sets the call id to the function name.
			return {
				role: "user",
				parts: [{ function_response: { name: result.toolCallId, response: result.isError ? { error: payload } : payload } }],
			};
		default:
			throw new Error(`The model client has no tool-result shape for engine "${engine}".`);
	}
}

export function toGatewayMessages(messages: readonly Message[]): GatewayMessage[] {
	const out: GatewayMessage[] = [];
	// Engine that answered the most recent assistant turn; its tool results follow it.
	let lastEngine: string | undefined;

	for (const message of messages) {
		if (message.role === "system") continue; // sent as system_prompt
		if (message.role === "user") {
			out.push({ role: "user", content: textOf(message.content) });
			continue;
		}
		if (message.role === "assistant") {
			const stored = decodeResponseId(message.responseId);
			if (stored) {
				lastEngine = stored.engine;
				out.push({ role: "assistant", content: stored.token });
			} else {
				// A turn with no token (an error, or a turn from another provider) replays as plain text.
				const text = textOf(message.content.filter((block) => block.type === "text"));
				if (text) out.push({ role: "assistant", content: text });
			}
			continue;
		}
		if (message.role === "toolResult") {
			if (!lastEngine) throw new Error("A tool result arrived before any assistant turn from the HTTP Gateway.");
			const shaped = toolResultMessage(lastEngine, message);
			const previous = out[out.length - 1];
			// Claude takes every result of one assistant turn in a single user message.
			if (
				(lastEngine === "claude" || lastEngine === "anthropic") &&
				previous?.role === "user" &&
				Array.isArray(previous.content) &&
				(previous.content as { type?: string }[]).every((block) => block.type === "tool_result")
			) {
				(previous.content as unknown[]).push(...(shaped.content as unknown[]));
			} else {
				out.push(shaped);
			}
		}
	}
	return out;
}
