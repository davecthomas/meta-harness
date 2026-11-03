/**
 * Runs pi in this process through its SDK, the way the harness server will,
 * with every model call going through the model client to ai-api-unified.
 *
 * Environment:
 *   GATEWAY_URL      HTTP Gateway base URL (default http://localhost:8080)
 *   GATEWAY_API_KEY  key the gateway accepts (the run token, in the full design)
 *   SAMPLE_ENGINE    engine the request names (default claude)
 *   SAMPLE_MODEL     model the request names (default claude-haiku-4-5)
 *   SAMPLE_TASK      the prompt pi runs (default: write hello.txt and read it back)
 */

import { randomUUID } from "node:crypto";
import { mkdtempSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { InMemoryCredentialStore } from "@earendil-works/pi-ai";
import {
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
	type ModelClientTurn,
} from "./model-client.ts";

const GATEWAY_URL = process.env.GATEWAY_URL ?? "http://localhost:8080";
const ENGINE = process.env.SAMPLE_ENGINE ?? "claude";
const MODEL = process.env.SAMPLE_MODEL ?? "claude-haiku-4-5";
if (!process.env.GATEWAY_API_KEY) {
	console.error("Set GATEWAY_API_KEY to a key the HTTP Gateway accepts (make serve uses local-dev-key).");
	process.exit(2);
}

// The sandbox image holds no provider keys. Drop any this shell passed in, so
// pi's built-in providers have nothing to authenticate with, as in the sandbox.
for (const name of Object.keys(process.env)) {
	if (name !== "GATEWAY_API_KEY" && /(_API_KEY|_AUTH_TOKEN|_OAUTH_TOKEN|^AWS_|^GOOGLE_APPLICATION_CREDENTIALS$)/.test(name)) {
		delete process.env[name];
	}
}

const log = (who: string, text: string) => console.log(`[${who}] ${text}`);
const short = (value: unknown, max = 160) => {
	const text = typeof value === "string" ? value : JSON.stringify(value);
	return text.length > max ? `${text.slice(0, max)}...` : text;
};

// 1. pi's model layer: no stored credentials, no models.json, no network catalog.
const agentDir = mkdtempSync(join(tmpdir(), "pi-agent-"));
const modelRuntime = await ModelRuntime.create({
	credentials: new InMemoryCredentialStore(),
	modelsPath: null,
	allowModelNetwork: false,
});

// 2. The model client: one provider, one model, zero prices (the gateway prices each turn).
const sessionId = randomUUID();
let turnNumber = 0;
const turns: ModelClientTurn[] = [];
modelRuntime.registerProvider(MODEL_CLIENT_PROVIDER, {
	name: "ai-api-unified through the HTTP Gateway",
	baseUrl: GATEWAY_URL,
	apiKey: "$GATEWAY_API_KEY",
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
		gatewayUrl: GATEWAY_URL,
		engine: ENGINE,
		model: MODEL,
		cacheKey: sessionId,
		onTurn: (turn) => {
			turns.push(turn);
			const target = `${turn.requestedEngine}:${turn.requestedModel ?? "(engine default)"}`;
			log(
				"model client",
				`POST /v1/conversations/turn  requested=${target}  messages=${turn.messageCount}  tools=${turn.toolNames.join(",")}`,
			);
			if (turn.error) {
				log("ai-api-unified", `error: ${turn.error}`);
				return;
			}
			const r = turn.response!;
			const calls = r.tool_calls.map((c) => c.name).join(",") || "none";
			log(
				"ai-api-unified",
				`served by ${r.engine}:${r.model}  fallback=${r.served_by_fallback}  finish=${r.finish_reason}  tool_calls=${calls}  ` +
					`tokens in=${r.usage.input_tokens} out=${r.usage.output_tokens} cached=${r.usage.cached_input_tokens ?? 0}  ` +
					`usd=${r.usd_cost ?? "n/a"}  cache_hit=${r.cache_hit}`,
			);
		},
	}),
});

const reachable = await modelRuntime.getAvailable();
log("pi", `models pi can reach: ${reachable.map((m) => `${m.provider}/${m.id}`).join(", ") || "none"}`);
if (reachable.length !== 1 || reachable[0]!.provider !== MODEL_CLIENT_PROVIDER) {
	console.error("pi can reach a provider other than the model client. Remove provider keys from the environment.");
	process.exit(1);
}
const model = modelRuntime.getModel(MODEL_CLIENT_PROVIDER, MODEL_CLIENT_MODEL);
if (!model) throw new Error("The model client's model is not registered.");

// 3. A fresh workspace, settings in memory, and no extensions, skills, prompt templates or context files.
const cwd = mkdtempSync(join(tmpdir(), "pi-workspace-"));
const settingsManager = SettingsManager.inMemory({
	retry: { enabled: false },
	cacheWarming: "off",
});
const resourceLoader = new DefaultResourceLoader({
	cwd,
	agentDir,
	settingsManager,
	noExtensions: true,
	noSkills: true,
	noPromptTemplates: true,
	noThemes: true,
	noContextFiles: true,
});
await resourceLoader.reload();

const { session } = await createAgentSession({
	cwd,
	agentDir,
	model,
	thinkingLevel: "off",
	modelRuntime,
	settingsManager,
	resourceLoader,
	sessionManager: SessionManager.inMemory(cwd),
	tools: ["read", "write", "edit", "ls"],
});

log("pi", `workspace ${cwd}`);
log("pi", `active tools: ${session.getActiveToolNames().join(", ")}`);

session.subscribe((event) => {
	switch (event.type) {
		case "turn_start":
			turnNumber += 1;
			log("pi", `turn ${turnNumber} starts`);
			break;
		case "tool_execution_start":
			log("pi", `runs tool ${event.toolName} ${short(event.args)}`);
			break;
		case "tool_execution_end":
			log("pi", `tool ${event.toolName} ${event.isError ? "failed" : "done"}: ${short(event.result?.content?.[0]?.text ?? event.result)}`);
			break;
		case "message_end":
			if (event.message.role === "assistant") {
				const text = event.message.content
					.filter((block) => block.type === "text")
					.map((block) => block.text)
					.join("");
				if (text) log("pi", `assistant text: ${short(text, 300)}`);
				if (event.message.stopReason === "error") log("pi", `assistant error: ${event.message.errorMessage}`);
			}
			break;
	}
});

const task =
	process.env.SAMPLE_TASK ||
	"Create a file named hello.txt containing exactly one line: a short friendly greeting. " +
		"Then read the file back and tell me what it says.";
log("pi", `task: ${task}`);

try {
	await session.prompt(task);
} finally {
	session.dispose();
}

const files = readdirSync(cwd);
log("workspace", `files: ${files.join(", ") || "none"}`);
if (files.includes("hello.txt")) log("workspace", `hello.txt: ${readFileSync(join(cwd, "hello.txt"), "utf8").trim()}`);

const answered = turns.filter((t) => t.response);
const usd = answered.reduce((sum, t) => sum + Number(t.response!.usd_cost ?? 0), 0);
const tokensIn = answered.reduce((sum, t) => sum + t.response!.usage.input_tokens, 0);
const tokensOut = answered.reduce((sum, t) => sum + t.response!.usage.output_tokens, 0);
log(
	"run",
	`model turns=${turns.length}  failed=${turns.length - answered.length}  tokens in=${tokensIn} out=${tokensOut}  usd=${usd.toFixed(6)}`,
);
