/**
 * The harness server: the Run API over pi's SDK (technical design section 8.2).
 *
 *   node server/main.ts
 *
 * Environment:
 *   HARNESS_SERVER_TOKEN  required. Every request must send `Authorization: Bearer <token>`.
 *   PORT, HOST            default 8090, 127.0.0.1. PORT=0 picks a free port.
 *   GATEWAY_URL           HTTP Gateway base URL (default http://localhost:8080)
 *   WORKSPACE_DIR         pi's working directory (default: a new temporary directory).
 *                         In the sandbox image it is /workspace, with the repo cloned in it.
 *   SESSION_DIR           where pi writes the session file (default: a new temporary directory)
 *   HARNESS_ALLOW_BASH    1 lets a profile name the bash tool. Leave it off outside a sandbox.
 *   HEARTBEAT_SECONDS     heartbeat event interval while a run is active (default 5)
 *
 * Runs under plain `node`, which strips the TypeScript types. Node 22.19 or later.
 */

import { timingSafeEqual } from "node:crypto";
import { mkdtempSync } from "node:fs";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { RunEvent } from "./event-log.ts";
import { Harness, HttpError, type FollowupRequest, type Profile, type RunRequest } from "./harness.ts";

const log = (text: string) => console.log(`[harness-server] ${text}`);

const SERVER_TOKEN = process.env.HARNESS_SERVER_TOKEN;
if (!SERVER_TOKEN) {
	console.error("Set HARNESS_SERVER_TOKEN. Every request must send it as a bearer token.");
	process.exit(2);
}

// The sandbox holds no provider keys. Drop any the environment passed in, so pi's
// built-in providers have nothing to authenticate with.
for (const name of Object.keys(process.env)) {
	if (/(_API_KEY|_AUTH_TOKEN|_OAUTH_TOKEN|^AWS_|^GOOGLE_APPLICATION_CREDENTIALS$)/.test(name)) delete process.env[name];
}

const harness = await Harness.create({
	gatewayUrl: process.env.GATEWAY_URL ?? "http://localhost:8080",
	workspaceDir: process.env.WORKSPACE_DIR || mkdtempSync(join(tmpdir(), "harness-workspace-")),
	sessionDir: process.env.SESSION_DIR || mkdtempSync(join(tmpdir(), "harness-sessions-")),
	agentDir: mkdtempSync(join(tmpdir(), "harness-agent-")),
	allowBash: process.env.HARNESS_ALLOW_BASH === "1",
	heartbeatSeconds: Number(process.env.HEARTBEAT_SECONDS ?? 5),
	log,
});

harness.events.subscribe((event) => log(`#${event.id} ${event.run_id} ${event.type} ${summarize(event)}`));

const MAX_BODY_BYTES = 1_000_000;
const ID_PATTERN = /^[A-Za-z0-9._:-]{1,200}$/;
const SESSION_ID_PATTERN = /^[A-Za-z0-9._-]{1,128}$/;

function summarize(event: RunEvent): string {
	const d = event.data;
	switch (event.type) {
		case "model_call":
			return d.error
				? `error: ${d.error}`
				: `${d.engine}:${d.model} finish=${d.finish_reason} tokens in=${d.input_tokens} out=${d.output_tokens} usd=${d.usd}`;
		case "tool_call":
			return `${d.tool} ${JSON.stringify(d.args).slice(0, 120)}`;
		case "tool_result":
			return `${d.tool} ${d.is_error ? "failed" : "done"}`;
		case "file_changed":
			return `${d.path}`;
		case "run_finished":
			return `${d.status} ${d.end_reason}`;
		default:
			return JSON.stringify(d).slice(0, 120);
	}
}

function authorized(request: IncomingMessage): boolean {
	const header = request.headers.authorization ?? "";
	const presented = Buffer.from(header.startsWith("Bearer ") ? header.slice(7) : "");
	const expected = Buffer.from(SERVER_TOKEN!);
	return presented.length === expected.length && timingSafeEqual(presented, expected);
}

function send(response: ServerResponse, status: number, body: unknown): void {
	response.writeHead(status, { "content-type": "application/json" });
	response.end(JSON.stringify(body));
}

async function readJson(request: IncomingMessage): Promise<Record<string, unknown>> {
	const chunks: Buffer[] = [];
	let size = 0;
	for await (const chunk of request) {
		size += (chunk as Buffer).length;
		if (size > MAX_BODY_BYTES) throw new HttpError(413, "The request body is over 1 MB.");
		chunks.push(chunk as Buffer);
	}
	try {
		const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
		if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed as Record<string, unknown>;
	} catch {
		// fall through
	}
	throw new HttpError(400, "The request body must be a JSON object.");
}

function text(body: Record<string, unknown>, field: string, pattern?: RegExp): string {
	const value = body[field];
	if (typeof value !== "string" || value.length === 0) throw new HttpError(400, `"${field}" must be a non-empty string.`);
	if (pattern && !pattern.test(value)) throw new HttpError(400, `"${field}" has characters or a length the server does not accept.`);
	return value;
}

function stringList(value: unknown, field: string): string[] {
	if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) {
		throw new HttpError(400, `"${field}" must be a list of strings.`);
	}
	return value as string[];
}

function positive(value: unknown, field: string): number {
	if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) throw new HttpError(400, `"${field}" must be a positive number.`);
	return value;
}

function parseProfile(value: unknown): Profile {
	if (!value || typeof value !== "object") throw new HttpError(400, `"profile" must be an object.`);
	const p = value as Record<string, unknown>;
	const tools = stringList(p.tools, "profile.tools");
	if (tools.length === 0) throw new HttpError(400, `"profile.tools" must name at least one tool.`);
	return {
		name: text(p, "name"),
		tools,
		max_turns: Math.floor(positive(p.max_turns, "profile.max_turns")),
		max_minutes: positive(p.max_minutes, "profile.max_minutes"),
	};
}

function parseRun(body: Record<string, unknown>): RunRequest {
	return {
		run_id: text(body, "run_id", ID_PATTERN),
		harness_session_id: text(body, "harness_session_id", SESSION_ID_PATTERN),
		prompt: text(body, "prompt"),
		acceptance_checks: body.acceptance_checks === undefined ? [] : stringList(body.acceptance_checks, "acceptance_checks"),
		profile: parseProfile(body.profile),
		grant_key: text(body, "grant_key"),
	};
}

function parseFollowup(body: Record<string, unknown>): FollowupRequest {
	return {
		run_id: text(body, "run_id", ID_PATTERN),
		prompt: text(body, "prompt"),
		grant_key: text(body, "grant_key"),
	};
}

/** GET /events: every event after Last-Event-ID (or ?after=), then each new one as it happens. */
function streamEvents(request: IncomingMessage, response: ServerResponse, url: URL): void {
	const header = request.headers["last-event-id"];
	const after = Number((Array.isArray(header) ? header[0] : header) ?? url.searchParams.get("after") ?? 0) || 0;
	response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "keep-alive" });
	const write = (event: RunEvent) => response.write(`id: ${event.id}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
	for (const event of harness.events.after(after)) write(event);
	const unsubscribe = harness.events.subscribe(write);
	const keepalive = setInterval(() => response.write(": keepalive\n\n"), 15_000);
	request.on("close", () => {
		unsubscribe();
		clearInterval(keepalive);
	});
}

async function route(request: IncomingMessage, response: ServerResponse): Promise<void> {
	const url = new URL(request.url ?? "/", "http://harness-server");
	const key = `${request.method} ${url.pathname}`;
	if (!authorized(request)) return send(response, 401, { error: "Send Authorization: Bearer <HARNESS_SERVER_TOKEN>." });
	switch (key) {
		case "GET /health": {
			const health = await harness.health();
			return send(response, health.ok ? 200 : 503, health);
		}
		case "POST /run": {
			const { created, run } = harness.start(parseRun(await readJson(request)));
			return send(response, created ? 202 : 200, run);
		}
		case "POST /followup": {
			const { created, run } = harness.followup(parseFollowup(await readJson(request)));
			return send(response, created ? 202 : 200, run);
		}
		case "GET /events":
			return streamEvents(request, response, url);
		case "GET /status": {
			const run = harness.getRun(url.searchParams.get("run_id"));
			if (!run) return send(response, 404, { error: "No run with that id." });
			return send(response, 200, { ...run, last_event_id: harness.events.lastId });
		}
		case "POST /cancel": {
			const run = await harness.cancel();
			if (!run) return send(response, 404, { error: "No run to cancel." });
			return send(response, 200, run);
		}
		case "GET /usage": {
			const run = harness.getRun(url.searchParams.get("run_id"));
			if (!run) return send(response, 404, { error: "No run with that id." });
			return send(response, 200, { run_id: run.run_id, status: run.status, ...run.usage });
		}
		default:
			return send(response, 404, { error: `No route ${key}.` });
	}
}

const server = createServer((request, response) => {
	route(request, response).catch((error: unknown) => {
		const status = error instanceof HttpError ? error.status : 500;
		if (status === 500) log(`error: ${error instanceof Error ? (error.stack ?? error.message) : String(error)}`);
		if (!response.headersSent) send(response, status, { error: error instanceof Error ? error.message : String(error) });
		else response.end();
	});
});

server.listen(Number(process.env.PORT ?? 8090), process.env.HOST ?? "127.0.0.1", () => {
	const address = server.address();
	const port = typeof address === "object" && address ? address.port : process.env.PORT;
	log(`listening on http://${process.env.HOST ?? "127.0.0.1"}:${port}, workspace ${harness.workspaceDir}`);
});

let stopping = false;
async function shutdown(signal: string): Promise<void> {
	if (stopping) return;
	stopping = true;
	log(`${signal} received, stopping`);
	await harness.shutdown();
	server.closeAllConnections();
	server.close();
	process.exit(0);
}
process.on("SIGTERM", () => void shutdown("SIGTERM"));
process.on("SIGINT", () => void shutdown("SIGINT"));
