// The words for where a task or a goal stands: one label, one meaning and one color for each, used wherever a
// status shows. The service's own names (review_unresolved, budget_exhausted) never reach a page.

export interface StatusWords {
  label: string;
  // What it means, as a sentence: the badge's hover text.
  meaning: string;
  color: string;
}

export const STATUSES: Record<string, StatusWords> = {
  // A task.
  queued: { label: "Queued", color: "gray", meaning: "It has not started: it waits for a free place to run." },
  blocked: { label: "Blocked", color: "yellow", meaning: "It has not started: it waits for another issue to be done first." },
  running: { label: "Working", color: "blue", meaning: "An agent is working on it now." },
  in_review: { label: "In review", color: "violet", meaning: "Its pull request is open, and its checks, review and fixes are under way." },
  taken_over: { label: "Taken over", color: "grape", meaning: "A person took the work over from the agent." },
  ready: { label: "Ready to merge", color: "teal", meaning: "Its review came back clean, and its pull request waits to be merged." },
  review_unresolved: { label: "Review unresolved", color: "orange", meaning: "Its review ended with findings no fix settled. A person decides what happens next." },
  closed: { label: "Done", color: "green", meaning: "Its work is finished: its pull request merged, or someone marked it done." },
  failed: { label: "Failed", color: "red", meaning: "Its runs ended without finishing the work. Its page says why and what to do." },
  canceled: { label: "Canceled", color: "dark", meaning: "It was stopped, by a person or because its goal or issue ended." },
  refused: { label: "Not started", color: "orange", meaning: "The planner made no task for the issue. Its row says what the issue lacks." },
  // A goal.
  planning: { label: "Planning", color: "indigo", meaning: "A planning task is drafting its issues, for you to review and approve." },
  active: { label: "Active", color: "teal", meaning: "It takes in issues and runs their tasks." },
  paused: { label: "Paused", color: "yellow", meaning: "It takes in no issue and starts no task until it is resumed." },
  not_started: { label: "Not started", color: "cyan", meaning: "It was saved and never started. Start begins it." },
  stalled: { label: "Stalled", color: "orange", meaning: "It has work left and none of it is moving." },
  budget_exhausted: { label: "Out of budget", color: "orange", meaning: "It reached its budget, and starts no task until the budget is raised." },
  met: { label: "Met", color: "green", meaning: "Every issue it held is done." },
};

// The words for a status. One the table does not hold reads as its own name, spaced, with no meaning to give.
export function statusWords(status: string): StatusWords {
  const spaced = status.replace(/_/g, " ");
  return STATUSES[status] ?? { label: spaced.charAt(0).toUpperCase() + spaced.slice(1), meaning: "", color: "gray" };
}

export const statusLabel = (status: string): string => statusWords(status).label;
