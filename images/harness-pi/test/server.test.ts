/**
 * Tests the harness server end to end against a fake HTTP Gateway, so no
 * model is called and nothing is billed. Each test starts its own server
 * process, because a server holds one session.
 *
 *   npm test
 */

import assert from "node:assert/strict";
import { type ChildProcess, spawn } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";

const PACKAGE_DIR = dirname(dirname(fileURLToPath(import.meta.url)));
const SERVER_TOKEN = "test-server-token";

// ---------------------------------------------------------------- fake gateway

type TurnReply = { status?: number; delayMs?: number; body: Record<string, unknown> };
interface FakeGateway {
	url: string;
	requests: { authorization: string | undefined; body: Record<string, unknown> }[];
	close: () => Promise<void>;
}

function turn(fields: { text?: string | null; tool_calls?: unknown[]; finish_reason: string; token: string }): Record<string, unknown> {
	return {
		text: fields.text ?? null,
		tool_calls: fields.tool_calls ?? [],
		finish_reason: fields.finish_reason,
		usage: {
			input_tokens: 100,
			output_tokens: 20,
			cached_input_tokens: 40,
			cache_write_5m_tokens: 0,
			cache_write_1h_tokens: 10,
			total_tokens: 120,
		},
		usd_cost: "0.001000",
		cache_hit: false,
		conversation_token: fields.token,
		engine: "claude",
		model: "claude-haiku-4-5",
		served_by_fallback: false,
	};
}

const WRITE_TURN = turn({
	finish_reason: "tool_use",
	tool_calls: [{ id: "toolu_1", name: "write", input: { path: "hello.txt", content: "hi there\n" } }],
	token: "tok-1",
});
const DONE_TURN = turn({ text: "Done.", finish_reason: "complete", token: "tok-2" });
const LS_TURN = turn({ finish_reason: "tool_use", tool_calls: [{ id: "toolu_ls", name: "ls", input: {} }], token: "tok-ls" });

async function startGateway(reply: (callNumber: number) => TurnReply): Promise<FakeGateway> {
	const requests: FakeGateway["requests"] = [];
	const server: Server = createServer(async (request, response) => {
		if (request.url === "/healthz") {
			response.writeHead(200, { "content-type": "application/json" });
			return response.end('{"status":"ok"}');
		}
		const chunks: Buffer[] = [];
		for await (const chunk of request) chunks.push(chunk as Buffer);
		requests.push({ authorization: request.headers.authorization, body: JSON.parse(Buffer.concat(chunks).toString("utf8")) });
		const r = reply(requests.length);
		if (r.delayMs) {
			// Ends early when the caller hangs up, so a pending delay never holds the test process open.
			await new Promise<void>((resolve) => {
				const timer = setTimeout(resolve, r.delayMs);
				response.once("close", () => {
					clearTimeout(timer);
					resolve();
				});
			});
		}
		if (response.destroyed) return;
		response.writeHead(r.status ?? 200, { "content-type": "application/json" });
		response.end(JSON.stringify(r.body));
	});
	await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
	const address = server.address() as { port: number };
	return {
		url: `http://127.0.0.1:${address.port}`,
		requests,
		close: () =>
			new Promise((resolve) => {
				server.closeAllConnections();
				server.close(() => resolve());
			}),
	};
}

// ---------------------------------------------------------------- harness server process

interface HarnessServer {
	url: string;
	workspace: string;
	process: ChildProcess;
	output: () => string;
	stop: () => Promise<void>;
}

const started: HarnessServer[] = [];
after(async () => {
	for (const server of started) await server.stop();
});

async function startServer(gatewayUrl: string): Promise<HarnessServer> {
	const workspace = mkdtempSync(join(tmpdir(), "harness-test-workspace-"));
	const child = spawn(process.execPath, ["server/main.ts"], {
		cwd: PACKAGE_DIR,
		env: {
			PATH: process.env.PATH,
			HOME: process.env.HOME,
			HARNESS_SERVER_TOKEN: SERVER_TOKEN,
			PORT: "0",
			GATEWAY_URL: gatewayUrl,
			WORKSPACE_DIR: workspace,
			HEARTBEAT_SECONDS: "1",
		},
		stdio: ["ignore", "pipe", "pipe"],
	});
	let output = "";
	child.stdout!.on("data", (chunk) => (output += chunk));
	child.stderr!.on("data", (chunk) => (output += chunk));
	const url = await new Promise<string>((resolve, reject) => {
		const timer = setTimeout(() => reject(new Error(`server did not start:\n${output}`)), 15_000);
		const check = () => {
			const match = output.match(/listening on (http:\/\/[\d.]+:\d+)/);
			if (match) {
				clearTimeout(timer);
				child.stdout!.off("data", check);
				resolve(match[1]!);
			}
		};
		child.stdout!.on("data", check);
		child.on("exit", (code) => reject(new Error(`server exited with ${code}:\n${output}`)));
	});
	const server: HarnessServer = {
		url,
		workspace,
		process: child,
		output: () => output,
		stop: async () => {
			if (child.exitCode === null && child.signalCode === null) {
				child.kill("SIGKILL");
				await new Promise((resolve) => child.once("exit", resolve));
			}
		},
	};
	started.push(server);
	return server;
}

async function api(
	server: HarnessServer,
	method: string,
	path: string,
	body?: unknown,
	token: string | null = SERVER_TOKEN,
): Promise<{ status: number; json: Record<string, unknown> }> {
	const headers: Record<string, string> = { "content-type": "application/json" };
	if (token) headers.authorization = `Bearer ${token}`;
	const response = await fetch(`${server.url}${path}`, {
		method,
		headers,
		body: body === undefined ? undefined : JSON.stringify(body),
	});
	return { status: response.status, json: (await response.json()) as Record<string, unknown> };
}

interface SseEvent {
	id: number;
	type: string;
	run_id: string;
	data: Record<string, unknown>;
}

/** Reads GET /events until `run_finished` for `runId`, starting after `lastEventId`. */
async function eventsUntilFinished(server: HarnessServer, runId: string, lastEventId = 0): Promise<SseEvent[]> {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), 20_000);
	const response = await fetch(`${server.url}/events`, {
		headers: { authorization: `Bearer ${SERVER_TOKEN}`, "last-event-id": String(lastEventId) },
		signal: controller.signal,
	});
	const decoder = new TextDecoder();
	const events: SseEvent[] = [];
	let buffer = "";
	try {
		for await (const chunk of response.body!) {
			buffer += decoder.decode(chunk as Uint8Array, { stream: true });
			let boundary: number;
			while ((boundary = buffer.indexOf("\n\n")) >= 0) {
				const frame = buffer.slice(0, boundary);
				buffer = buffer.slice(boundary + 2);
				const data = frame.split("\n").find((line) => line.startsWith("data: "));
				if (!data) continue;
				const event = JSON.parse(data.slice(6)) as SseEvent;
				events.push(event);
				if (event.type === "run_finished" && event.run_id === runId) return events;
			}
		}
	} finally {
		clearTimeout(timer);
		controller.abort();
	}
	throw new Error(`the event stream ended before run_finished for ${runId}`);
}

function runBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		run_id: "task-1:0",
		harness_session_id: "task-1",
		prompt: "Create hello.txt containing a greeting.",
		acceptance_checks: ["test -f hello.txt"],
		profile: { name: "pi-lean", tools: ["read", "write", "edit", "ls"], max_turns: 10, max_minutes: 5 },
		token: "run-token-1",
		engine: "claude",
		model: "claude-haiku-4-5",
		...overrides,
	};
}

// ---------------------------------------------------------------- tests

test("rejects a request without the server token", async () => {
	const gateway = await startGateway(() => ({ body: DONE_TURN }));
	const server = await startServer(gateway.url);
	assert.equal((await api(server, "GET", "/health", undefined, null)).status, 401);
	assert.equal((await api(server, "GET", "/health", undefined, "wrong-token")).status, 401);
	const health = await api(server, "GET", "/health");
	assert.equal(health.status, 200);
	assert.equal((health.json.checks as Record<string, boolean>).gateway_reachable, true);
	await server.stop();
	await gateway.close();
});

test("runs a task, streams typed events, re-attaches by run_id, and continues the session in a follow-up", async () => {
	const gateway = await startGateway((n) => ({ body: n === 1 ? WRITE_TURN : DONE_TURN }));
	const server = await startServer(gateway.url);

	const run = await api(server, "POST", "/run", runBody());
	assert.equal(run.status, 202);
	assert.equal(run.json.run_id, "task-1:0");
	assert.equal(run.json.token, undefined, "the token never appears in run state");

	const events = await eventsUntilFinished(server, "task-1:0");
	const types = events.map((e) => e.type);
	for (const type of ["turn_started", "model_call", "tool_call", "tool_result", "file_changed", "turn_finished", "run_finished"]) {
		assert.ok(types.includes(type), `missing ${type} in ${types.join(",")}`);
	}
	assert.deepEqual(
		events.map((e) => e.id),
		events.map((_, i) => i + 1),
		"event ids count up from 1",
	);
	assert.equal(events.find((e) => e.type === "file_changed")!.data.path, "hello.txt");
	const finished = events.at(-1)!;
	assert.equal(finished.data.status, "FINISHED");
	assert.equal(finished.data.end_reason, "completed");
	assert.equal(readFileSync(join(server.workspace, "hello.txt"), "utf8"), "hi there\n");
	assert.deepEqual(readdirSync(server.workspace), ["hello.txt"], "pi's session file stays out of the workspace");

	// The model client sent the run's token, the session id as the cache key, and a Claude tool result.
	assert.equal(gateway.requests.length, 2);
	assert.equal(gateway.requests[0]!.authorization, "Bearer run-token-1");
	assert.deepEqual(gateway.requests[0]!.body.prompt_cache, { retention: "extended", key: "task-1" });
	const secondMessages = gateway.requests[1]!.body.messages as Record<string, unknown>[];
	assert.deepEqual(secondMessages[1], { role: "assistant", content: "tok-1" });
	assert.equal((secondMessages[2]!.content as Record<string, unknown>[])[0]!.type, "tool_result");

	const status = await api(server, "GET", "/status");
	assert.equal(status.json.status, "FINISHED");
	assert.equal(status.json.turns, 2);
	const usage = await api(server, "GET", "/usage?run_id=task-1:0");
	assert.deepEqual(
		{ ...usage.json },
		{
			run_id: "task-1:0",
			status: "FINISHED",
			input_tokens: 200,
			output_tokens: 40,
			cached_input_tokens: 80,
			cache_write_5m_tokens: 0,
			cache_write_1h_tokens: 20,
			calls: 2,
			failed_calls: 0,
			usd: 0.002,
		},
	);

	// The same run_id again re-attaches: no new pi run, no model call.
	const again = await api(server, "POST", "/run", runBody());
	assert.equal(again.status, 200);
	assert.equal(again.json.status, "FINISHED");
	assert.equal(gateway.requests.length, 2);

	// A different run_id cannot start a second session in this sandbox.
	assert.equal((await api(server, "POST", "/run", runBody({ run_id: "task-2:0", harness_session_id: "task-2" }))).status, 409);

	// Last-Event-ID resumes after the given event.
	const lastId = finished.id;
	const followup = await api(server, "POST", "/followup", {
		run_id: "task-1:followup-1",
		prompt: "Now say goodbye.",
		token: "run-token-2",
		engine: "claude",
		model: "claude-haiku-4-5",
	});
	assert.equal(followup.status, 202);
	assert.equal(followup.json.kind, "followup");
	assert.equal(followup.json.harness_session_id, "task-1");
	const followupEvents = await eventsUntilFinished(server, "task-1:followup-1", lastId);
	assert.equal(followupEvents[0]!.id, lastId + 1);
	assert.equal(followupEvents.at(-1)!.data.status, "FINISHED");
	assert.equal(gateway.requests.length, 3);
	assert.equal(gateway.requests[2]!.authorization, "Bearer run-token-2");
	const followupMessages = gateway.requests[2]!.body.messages as Record<string, unknown>[];
	assert.ok(followupMessages.length >= 5, "the follow-up sends the session's earlier turns");
	assert.deepEqual(followupMessages.at(-1), { role: "user", content: "Now say goodbye." });

	await server.stop();
	await gateway.close();
});

test("cancel stops pi during a model call and ends the run CANCELED", async () => {
	const gateway = await startGateway(() => ({ delayMs: 30_000, body: DONE_TURN }));
	const server = await startServer(gateway.url);
	await api(server, "POST", "/run", runBody());
	while (gateway.requests.length === 0) await new Promise((resolve) => setTimeout(resolve, 50));

	const startedAt = Date.now();
	const canceled = await api(server, "POST", "/cancel");
	assert.equal(canceled.status, 200);
	assert.equal(canceled.json.status, "CANCELED");
	assert.ok(Date.now() - startedAt < 5_000, "cancel returns within seconds, not when the model call ends");
	const events = await eventsUntilFinished(server, "task-1:0");
	assert.equal(events.find((e) => e.type === "model_call")!.data.aborted, true);
	await server.stop();
	await gateway.close();
});

test("the turn limit ends the run with ERROR max_turns", async () => {
	const gateway = await startGateway(() => ({ body: LS_TURN }));
	const server = await startServer(gateway.url);
	const profile = { name: "pi-lean", tools: ["ls"], max_turns: 2, max_minutes: 5 };
	await api(server, "POST", "/run", runBody({ profile }));
	const events = await eventsUntilFinished(server, "task-1:0");
	const finished = events.at(-1)!;
	assert.equal(finished.data.status, "ERROR");
	assert.equal(finished.data.end_reason, "max_turns");
	assert.equal(gateway.requests.length, 2);
	await server.stop();
	await gateway.close();
});

test("a failed gateway call ends the run with ERROR model_error", async () => {
	const gateway = await startGateway(() => ({ status: 500, body: { detail: "provider down" } }));
	const server = await startServer(gateway.url);
	await api(server, "POST", "/run", runBody());
	const events = await eventsUntilFinished(server, "task-1:0");
	const finished = events.at(-1)!;
	assert.equal(finished.data.status, "ERROR");
	assert.equal(finished.data.end_reason, "model_error");
	assert.match(String(finished.data.error), /provider down/);
	assert.equal(gateway.requests.length, 1, "pi does not retry; the gateway's fallback chain is the only retry");
	const usage = await api(server, "GET", "/usage");
	assert.equal(usage.json.failed_calls, 1);
	await server.stop();
	await gateway.close();
});

test("rejects bad run requests", async () => {
	const gateway = await startGateway(() => ({ body: DONE_TURN }));
	const server = await startServer(gateway.url);
	const bash = { name: "pi-lean", tools: ["read", "bash"], max_turns: 10, max_minutes: 5 };
	assert.equal((await api(server, "POST", "/run", runBody({ profile: bash }))).status, 400);
	assert.equal((await api(server, "POST", "/run", runBody({ prompt: "" }))).status, 400);
	assert.equal((await api(server, "POST", "/run", runBody({ harness_session_id: "../escape" }))).status, 400);
	assert.equal((await api(server, "GET", "/status")).status, 404);
	assert.equal(gateway.requests.length, 0);
	await server.stop();
	await gateway.close();
});

test("SIGTERM during a run stops pi and exits within seconds", async () => {
	const gateway = await startGateway(() => ({ delayMs: 30_000, body: DONE_TURN }));
	const server = await startServer(gateway.url);
	await api(server, "POST", "/run", runBody());
	while (gateway.requests.length === 0) await new Promise((resolve) => setTimeout(resolve, 50));
	const startedAt = Date.now();
	server.process.kill("SIGTERM");
	const code = await new Promise((resolve) => server.process.once("exit", resolve));
	assert.equal(code, 0);
	assert.ok(Date.now() - startedAt < 5_000, `exit took ${Date.now() - startedAt} ms`);
	assert.match(server.output(), /ERROR \(server_shutdown\)/);
	assert.ok(existsSync(server.workspace));
	await gateway.close();
});
