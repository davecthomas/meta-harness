/**
 * The Run API's event log: every typed event the harness server emits, in
 * order, with a sequence number that `GET /events` sends as the SSE id.
 *
 * A client that loses its connection reconnects with `Last-Event-ID` and gets
 * every event after that id, so no event is lost while it was away. The log
 * lives in memory for the life of the sandbox, which serves one task.
 */

export type RunEventType =
	| "turn_started"
	| "model_call"
	| "tool_call"
	| "tool_result"
	| "file_changed"
	| "compacted"
	| "turn_finished"
	| "review_submitted"
	| "plan_updated"
	| "heartbeat"
	| "run_finished";

export interface RunEvent {
	/** Sequence number, from 1, across every run this server holds. */
	id: number;
	type: RunEventType;
	run_id: string;
	time: string;
	data: Record<string, unknown>;
}

export type RunEventListener = (event: RunEvent) => void;

export class EventLog {
	private readonly events: RunEvent[] = [];
	private readonly listeners = new Set<RunEventListener>();

	append(type: RunEventType, runId: string, data: Record<string, unknown>): RunEvent {
		const event: RunEvent = { id: this.events.length + 1, type, run_id: runId, time: new Date().toISOString(), data };
		this.events.push(event);
		for (const listener of this.listeners) listener(event);
		return event;
	}

	/** Every event with an id greater than `id`. */
	after(id: number): RunEvent[] {
		return this.events.slice(Math.max(0, id));
	}

	/** Calls `listener` for each new event. Returns the function that unsubscribes. */
	subscribe(listener: RunEventListener): () => void {
		this.listeners.add(listener);
		return () => this.listeners.delete(listener);
	}

	get lastId(): number {
		return this.events.length;
	}
}
