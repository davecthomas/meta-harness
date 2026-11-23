import { moneyText } from "../../money";

// What went wrong with a task, for a developer: what happened in a sentence, why, and what to do about it.
// The control plane records a failure as one line of text (a task's status_reason); this reads that line and
// says the same thing plainly, with the fix. The recorded line stays available as `detail`, cleaned of the
// wrappers each layer added, for when the reader needs the exact error.

// What the reader can do from where the message is shown.
//   retry        send the task back to the queue
//   retry_more   the same, with a higher spend limit, since the last one was used up
//   task         open the task: its runs, its review, its budget hold
//   config       open Configuration
//   issue        open the issue on GitHub, to edit it
export type Remedy = "retry" | "retry_more" | "task" | "config" | "issue";

export interface Diagnosis {
  // The kind of failure: tasks that failed the same way share it, so a list can group them.
  kind: string;
  // What happened, as a short sentence with no period: "The run could not start".
  summary: string;
  // Why, in a sentence or two.
  cause: string;
  // What to do about it, as an instruction.
  fix: string;
  remedies: Remedy[];
  // The recorded error, without the wrappers around it.
  detail: string;
}

export interface Failure {
  status: string | null;
  reason: string | null;
  // "budget" while a running task is held for budget.
  waitingOn?: string | null;
  // Why the planner refused the issue, when it did.
  refused?: string | null;
}

// The wrappers the workflow and Temporal put around the error that matters.
function unwrap(reason: string): string {
  let text = reason.trim();
  text = text.replace(/^TaskFailed:\s*/, "");
  text = text.replace(/^run \d+ on \S+ ended for good:\s*/, "");
  text = text.replace(/^run_harness failed \((\w+)\):\s*(?:\1:\s*)?/, "");
  return text;
}

const usdText = (amount: string) => `$${Number(amount).toLocaleString("en-US", { minimumFractionDigits: 2 })}`;

interface Rule {
  kind: string;
  test: RegExp;
  write: (match: RegExpMatchArray, detail: string) => Omit<Diagnosis, "kind" | "detail">;
}

const CLEAN_CONFLICT = {
  summary: "The review is clean, but the pull request conflicts with its base branch",
  cause: "Resolving the conflict takes a fix round, and the review loop had used all of them. The review itself passed.",
  fix: "Merge it. The conflict is resolved first, then its checks and a review run, and it merges once that review is clean.",
  remedies: ["task", "retry"] as Remedy[],
};

// A task whose last review was clean and still ended unresolved: only a conflict with its base keeps a clean
// review from merging. Task records written before the workflow said so carry "used its N fix rounds".
export function cleanButConflicting(diagnosis: Diagnosis): Diagnosis {
  return { ...diagnosis, kind: "clean_conflict", ...CLEAN_CONFLICT };
}

// Checked in order: the first that matches the recorded reason wins.
const RULES: Rule[] = [
  {
    kind: "grant_spend_limit",
    test: /refused the run grant \(422\).*spend_limit_usd.*'input': '([0-9.]+)'/s,
    write: (m) => ({
      summary: "The task's run could not start: the HTTP Gateway refused its spend limit",
      cause: `The run asked for a ${usdText(m[1])} spend limit, and the HTTP Gateway grants at most $999,999.99. Under the track budget policy Octofusion sent one dollar too many; that is fixed in the control plane. No model was called and nothing was spent.`,
      fix: "Retry the task. If it fails the same way, the deployed control plane is older than the fix: check the last deploy.",
      remedies: ["retry"],
    }),
  },
  {
    kind: "grant_refused",
    test: /refused the run grant \((\d+)\)/,
    write: (m) => ({
      summary: "The task's run could not start: the HTTP Gateway refused its run grant",
      cause: `The HTTP Gateway answered ${m[1]} when the run asked for its grant, so no model was called. The recorded error says which field it refused.`,
      fix: "Check the tier's engine and model against what the HTTP Gateway serves, then retry the task.",
      remedies: ["retry", "config"],
    }),
  },
  {
    kind: "config",
    test: /HarnessConfigError/,
    write: (_, detail) => ({
      summary: "The task's run could not start: a setting is wrong",
      cause: `The run service refused to start the run: ${detail.slice(0, 200)}`,
      fix: "Fix the setting the error names (a harness profile, a tier or the worker's environment), then retry the task.",
      remedies: ["config", "retry"],
    }),
  },
  {
    kind: "spend_limit",
    test: /spent its (\$[0-9.,]+) spend limit|spend_limit/,
    write: (m) => ({
      summary: `The task used up its ${m[1] ?? "spend limit"}`,
      cause: "Its runs together reached the task's spend limit before the work was finished. What they changed is on the task's page.",
      fix: "Retry with a higher spend limit, or split the issue into smaller ones if it is too large for one task.",
      remedies: ["retry_more", "task"],
    }),
  },
  {
    kind: "no_change",
    test: /pi changed nothing/,
    write: (_, detail) => {
      // A second model read the base branch and found the work missing: what it found is the thing to act on.
      const missing = detail.match(/the base branch does not have the work yet: (.+)$/s)?.[1]?.trim();
      return missing
        ? {
            summary: "The task's run changed no file, and the work is not there yet",
            cause: `The agent ended its run with the repo as it found it. A second model then read the base branch and found the work missing: ${missing}`,
            fix: "Say in the issue what to change, with the file when you know it, then retry. If the issue is too large, split it.",
            remedies: ["issue", "task", "retry"],
          }
        : {
            summary: "The task's run finished without changing any file",
            cause: "The agent ended its run and left the repo as it found it, so there is no pull request. No second model was there to check whether the work is already done.",
            fix: "Read the run's last turn on the task's page. Close the issue if it is done; otherwise say in the issue what to change, then retry.",
            remedies: ["task", "issue", "retry"],
          };
    },
  },
  {
    kind: "limit",
    test: /\((turn_limit|time_limit|max_turns|max_minutes|timeout)\)|TimeoutError|timed out/i,
    write: () => ({
      summary: "The task's run stopped at its turn or time limit before it finished",
      cause: "The harness profile's limit ended the run mid-work. Nothing was published from it.",
      fix: "Retry the task. If it stops again, the issue is too large for one run: split it, or raise the profile's max_turns or max_minutes.",
      remedies: ["retry", "config"],
    }),
  },
  {
    kind: "model",
    test: /every candidate failed|model_error|none of the \d+ attempts finished/,
    write: () => ({
      summary: "Every model the task was routed to failed",
      cause: "Each candidate's run ended on a model or provider error, so the task ran out of candidates. This is usually a provider outage or a rate limit, not the task.",
      fix: "Retry the task once the provider answers again. If it keeps failing, check the HTTP Gateway's health on the Overview.",
      remedies: ["retry", "task"],
    }),
  },
  {
    kind: "infrastructure",
    test: /harness_error|server_shutdown|sandbox|worker|container/i,
    write: () => ({
      summary: "The sandbox or harness of the task's run stopped",
      cause: "The run ended on an infrastructure error (the sandbox, the harness server or a worker restart), not on the task's work.",
      fix: "Retry the task. If it fails again, its page links the warnings and errors logged for it.",
      remedies: ["retry", "task"],
    }),
  },
  {
    // The review passed, and the merge found the pull request conflicting with its base, with no fix round
    // left to resolve it: what is left is the conflict, not the review.
    kind: "clean_conflict",
    test: /came back clean.*conflicts with its base/,
    write: () => CLEAN_CONFLICT,
  },
  {
    kind: "fix_rounds",
    test: /used its (\d+) fix rounds/,
    write: (m) => ({
      summary: `The review still asked for changes after ${m[1]} fix rounds`,
      cause: "The pull request is open, and the last review's findings are not fixed. The review loop stops there to cap what the task spends.",
      fix: "Read the last review on the task's page. Then merge the pull request as it is, retry for another set of rounds, or fix the findings on the branch yourself.",
      remedies: ["task", "retry"],
    }),
  },
  {
    kind: "no_verdict",
    test: /got no verdict|the review loop stopped/,
    write: () => ({
      summary: "A review run ended without a verdict",
      cause: "The pull request is open, but its review did not finish, so nothing says whether it may merge.",
      fix: "Retry to review it again, or review the pull request yourself on the task's page.",
      remedies: ["retry", "task"],
    }),
  },
];

const NO_CHECKS = /no ## Acceptance checks/;

// What is wrong with a goal's issue or a task, and what to do. Null when nothing is.
export function diagnose(failure: Failure): Diagnosis | null {
  if (failure.refused) {
    const noChecks = NO_CHECKS.test(failure.refused);
    return {
      kind: noChecks ? "refused_no_checks" : "refused",
      summary: noChecks ? "Not started: the issue names no acceptance checks" : "Not started: the planner refused the issue",
      cause: noChecks
        ? "A task needs commands that prove its work, and neither the issue nor its repo's registry entry names any."
        : `The planner did not make a task for it: ${failure.refused}.`,
      fix: noChecks
        ? "Add an \"## Acceptance checks\" section to the issue with one command per line, or give the repo default checks in Configuration. The goal's next sweep reads the issue again and takes it in."
        : "Edit the issue so the reason no longer holds. The goal's next sweep reads it again.",
      remedies: noChecks ? ["issue", "config"] : ["issue"],
      detail: failure.refused,
    };
  }
  if (failure.waitingOn === "budget") {
    return {
      kind: "budget_hold",
      summary: "Paused: the task reached its spend limit",
      cause: "Its run is held, not ended: the work so far is kept, and it goes on as soon as it has more to spend.",
      fix: "Open the task and raise its spend limit to carry on, or end it there.",
      remedies: ["task"],
      detail: moneyText(failure.reason) ?? "",
    };
  }
  if (failure.status !== "failed" && failure.status !== "review_unresolved") return null;
  const recorded = failure.reason ?? "";
  const detail = moneyText(unwrap(recorded)) ?? "";
  for (const rule of RULES) {
    const match = recorded.match(rule.test);
    if (match) return { kind: rule.kind, detail, ...rule.write(match, detail) };
  }
  const unresolved = failure.status === "review_unresolved";
  return {
    kind: `other:${detail}`,
    summary: unresolved ? "The review loop ended without a clean review" : "The task failed",
    cause: detail ? `The control plane recorded: ${detail.slice(0, 240)}` : "No reason was recorded.",
    fix: unresolved
      ? "Read the last review on the task's page, then merge, retry or fix it yourself."
      : "Open the task for its runs and their logs. Retry it once the cause is fixed.",
    remedies: ["task", "retry"],
    detail,
  };
}

// The diagnosis in one line, for a list with no room for more: what happened, then what to do.
export function oneLine(diagnosis: Diagnosis): string {
  return `${diagnosis.summary}. ${diagnosis.fix}`;
}
