/**
 * The bash allowlist: a harness profile's `bash_allow` names the commands pi's bash tool may run.
 *
 * The harness server loads this as an inline pi extension. Its `tool_call`
 * handler reads each bash command before pi runs it, splits it into simple
 * commands at `&&`, `||`, `;`, `|`, `&` and newlines, and refuses the call
 * when any of them starts with a program the profile does not name. pi gets
 * the refusal as the tool's error result and can try another command.
 *
 * The allowlist keeps a run to the tools its task needs. It is not the
 * security boundary: an allowed `python` can do anything a shell can. The
 * per-run sandbox is the boundary. Command and process substitution, `$(...)`,
 * backticks and `<(...)`, are refused outright, because the program they run
 * is hidden from this check.
 */

import { type ExtensionAPI, isToolCallEventType } from "@earendil-works/pi-coding-agent";

/** Programs that only change how the next word runs. The check skips them and reads that word. */
const PREFIX_WORDS = new Set(["env", "time", "nice", "timeout", "command"]);

const SUBSTITUTION = /\$\(|`|<\(|>\(/;
const ASSIGNMENT = /^[A-Za-z_][A-Za-z0-9_]*=/;

/** The program each simple command in a bash command line runs, in order. */
export function programsIn(command: string): string[] {
	const programs: string[] = [];
	// Redirections such as 2>&1, >&2 and &> carry an "&" that separates nothing, so they go first.
	const unredirected = command.replace(/\d*>&\d*|&>>?/g, " ");
	for (const segment of unredirected.split(/&&|\|\||[;|&\n]/)) {
		const words = segment.trim().split(/\s+/).filter(Boolean);
		let index = 0;
		while (index < words.length) {
			const word = words[index]!;
			// A leading "(" or "{" opens a group; the program is the word after it.
			const bare = word.replace(/^[({]+/, "");
			if (bare === "" || ASSIGNMENT.test(bare)) {
				index += 1;
				continue;
			}
			if (PREFIX_WORDS.has(bare)) {
				index += 1;
				// `timeout 30 cmd`: skip the duration too.
				if (bare === "timeout" && /^\d/.test(words[index] ?? "")) index += 1;
				continue;
			}
			// A path such as ./scripts/x.sh or /usr/bin/git names its program by its last part.
			// A closing ")" or "}" of a group ends the word.
			programs.push(bare.replace(/[)}]+$/, "").split("/").pop()!);
			break;
		}
	}
	return programs;
}

/** Why the command is refused, or undefined when every program in it is on the allowlist. */
export function refusal(command: string, allow: readonly string[]): string | undefined {
	if (SUBSTITUTION.test(command)) {
		return "Command and process substitution ($(...), backticks, <(...)) is not allowed. Run each command on its own.";
	}
	const allowed = new Set(allow);
	const refused = programsIn(command).filter((program) => !allowed.has(program));
	if (refused.length === 0) return undefined;
	return `This run's harness profile does not allow ${[...new Set(refused)].join(", ")}. Allowed: ${[...allowed].sort().join(", ")}.`;
}

/** The inline pi extension: refuses bash calls outside `allow`. */
export function bashAllowlist(allow: readonly string[]) {
	return (pi: ExtensionAPI) => {
		pi.on("tool_call", (event) => {
			if (!isToolCallEventType("bash", event)) return undefined;
			const reason = refusal(event.input.command, allow);
			return reason ? { block: true, reason } : undefined;
		});
	};
}
