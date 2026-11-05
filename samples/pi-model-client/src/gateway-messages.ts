/**
 * Converts pi's transcript into the message list `/v1/conversations/turn` takes.
 *
 * - User text goes as `{role: "user", content}`, which every engine accepts.
 * - An assistant turn goes back as its `conversation_token`, which the HTTP
 *   Gateway expands into the answering engine's own shape.
 * - A tool result goes as `{type: "tool_result", tool_call_id, result,
 *   is_error}`, the same on every engine. The HTTP Gateway (ai-api-unified-http
 *   1.19.0) builds the answering engine's own message from it, and puts the
 *   results of one turn together where the engine needs that.
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

function toolResultMessage(result: ToolResultMessage): GatewayMessage {
	return {
		type: "tool_result",
		tool_call_id: result.toolCallId,
		result: { output: textOf(result.content) },
		is_error: result.isError,
	};
}

export function toGatewayMessages(messages: readonly Message[]): GatewayMessage[] {
	const out: GatewayMessage[] = [];
	for (const message of messages) {
		if (message.role === "system") continue; // sent as system_prompt
		if (message.role === "user") {
			out.push({ role: "user", content: textOf(message.content) });
			continue;
		}
		if (message.role === "assistant") {
			const stored = decodeResponseId(message.responseId);
			if (stored) {
				out.push({ role: "assistant", content: stored.token });
			} else {
				// A turn with no token (an error, or an aborted turn) replays as plain text.
				const text = textOf(message.content.filter((block) => block.type === "text"));
				if (text) out.push({ role: "assistant", content: text });
			}
			continue;
		}
		if (message.role === "toolResult") out.push(toolResultMessage(message));
	}
	return out;
}
