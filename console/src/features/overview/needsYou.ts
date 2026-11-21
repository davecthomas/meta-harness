import { useCallback, useEffect, useState } from "react";
import { api, goalTitle, type Goal, type Task } from "../../api";
import { diagnose, oneLine } from "../tasks/diagnosis";
import { standingLine } from "../tasks/Standing";
import { moneyText } from "../../money";

// What needs the operator, as one list with one count. The Overview shows all of it, and the header's inbox
// its count and first few: both read this, so the two never disagree. Each item is named by what it asks for
// ("Merge", "Raise the budget"), since "needs you" alone says nothing.
//
// What is not in it, on purpose: an agent's review in progress, which the operator may join but need not; and
// the kill switch, which has its own banner on every page.

export interface NeedsYouItem {
  key: string;
  // What it asks for, in a word or three: the label the item leads with.
  action: string;
  // What it is about: the task's or goal's title.
  title: string;
  to: string;
  // Why it is here and what to do, as a sentence or two.
  detail: string;
  // A page outside the console that settles it, such as the pull request to merge.
  href?: string;
  hrefLabel?: string;
  // What it is about, for a page that offers the action inline.
  task?: Task;
  goal?: Goal;
  // Since when it has waited, when the supervisor said.
  since?: string;
}

// The goals worth asking about, and how often the list is read again.
export const NEEDS_YOU_GOALS = ["active", "budget_exhausted", "stalled"];
export const NEEDS_YOU_POLL_MS = 30_000;
const MOST = 200;
// How many of a stuck goal's waiting tasks its line names.
const MOST_NAMED = 3;

function goalItems(g: Goal): NeedsYouItem[] {
  const items: NeedsYouItem[] = [];
  const base = { title: goalTitle(g), to: `/goals/${g.id}`, goal: g };
  // A goal past its budget, once: its key is the same whether it is over or stopped.
  if (g.status === "budget_exhausted" || g.budget_state === "blocked") {
    items.push({ ...base, key: `goal-${g.id}`, action: "Raise the budget", detail: "Its budget stopped it: none of its tasks starts until it has more to spend. Raise the budget, or leave it stopped." });
  } else if (g.budget_state === "over") {
    items.push({ ...base, key: `goal-${g.id}`, action: "Over budget", detail: "It is past its budget and keeps running. Raise the budget, or pause the goal." });
  }
  // A goal with work left and none of it moving: everything it has left waits on the operator.
  if (g.standing?.state === "needs_you") {
    const reason = `${g.standing.reason.charAt(0).toUpperCase()}${g.standing.reason.slice(1)}`;
    // Named, so the operator sees which tasks without opening the goal: three at most, then how many more.
    const yours = g.standing.yours ?? [];
    const named = yours.slice(0, MOST_NAMED).map((t) => t.title).join(", ");
    const more = yours.length > MOST_NAMED ? ` and ${yours.length - MOST_NAMED} more` : "";
    const waiting = named ? ` Waiting on you: ${named}${more}.` : "";
    items.push({ ...base, key: `goal-waits-${g.id}`, action: "Unstick the goal", detail: `${reason}.${waiting} The goal goes on as soon as one of them is unstuck.` });
  }
  return items;
}

function taskItem(t: Task): NeedsYouItem | null {
  const base = { key: `task-${t.id}`, title: t.title, to: `/tasks/${t.id}`, task: t, since: t.standing?.since };
  if (t.waiting_on === "budget") {
    const asked = t.budget_request?.for === "task" && t.budget_request.add_usd ? `$${Number(t.budget_request.add_usd).toFixed(2)}` : null;
    return {
      ...base,
      // Named by the amount when the service could say what the next run needs.
      action: asked ? `Grant ${asked} more` : t.budget_request?.for === "goal" ? "Raise the goal's budget" : "Raise the spend limit",
      detail: `${moneyText(t.status_reason) ?? "It reached its spend limit"}. Open it to ${asked ? `grant the ${asked} in one click, set another limit,` : "raise the limit"} or end it.`,
    };
  }
  if (t.waiting_on === "operator_review") {
    return { ...base, action: "Finish the review", detail: `${t.review_claimed_by ?? "An operator"} took its review over: the task waits for that verdict.` };
  }
  if (t.waiting_on === "review") {
    return { ...base, action: "Decide on the review", detail: "Its review asks for changes and waits for you: dismiss findings, or let the fix run start." };
  }
  if (t.status === "ready") {
    return {
      ...base,
      action: "Merge",
      detail: "Its review is clean and its pull request is open. Merge it with Merge on the task's page, or on GitHub.",
      href: t.pr_url && /^https?:/.test(t.pr_url) ? t.pr_url : undefined,
      hrefLabel: "Open the pull request",
    };
  }
  if (t.status === "review_unresolved" || t.status === "failed") {
    const diagnosis = diagnose({ status: t.status, reason: t.status_reason });
    return {
      ...base,
      action: t.status === "failed" ? "Retry or fix" : "Settle the review",
      detail: diagnosis ? oneLine(diagnosis) : t.standing ? standingLine(t.standing) : "Open it to see what happened and what to do.",
    };
  }
  // Anything else the supervisor found to be an operator's: stuck behind another task, waiting with no goal
  // to move it. Its standing says why and what to do.
  if (t.standing?.owner === "operator") return { ...base, action: "Look at", detail: standingLine(t.standing) };
  return null;
}

// The one list. tasks: the tasks the supervisor found an operator to have (others are left out); goals: the open ones.
export function needsYouItems(tasks: Task[], goals: Goal[]): NeedsYouItem[] {
  return [...goals.flatMap(goalItems), ...tasks.flatMap((t) => taskItem(t) ?? [])];
}

// The line under the count: what it is made of, by what each asks for, so it is never a bare number.
export function needsYouNote(items: NeedsYouItem[] | null): string {
  if (items === null) return "";
  if (!items.length) return "Nothing needs you.";
  const counts = new Map<string, number>();
  for (const item of items) counts.set(item.action, (counts.get(item.action) ?? 0) + 1);
  return `${[...counts].map(([action, n]) => `${n} to ${action.charAt(0).toLowerCase()}${action.slice(1)}`).join(", ")}.`;
}

// The list as it is now, read again every NEEDS_YOU_POLL_MS and whenever `refresh` changes. A read that
// fails keeps the list it had and says why: an empty list must mean nothing needs the operator.
export function useNeedsYou(refresh = 0): { items: NeedsYouItem[] | null; error: string | null; reload: () => void } {
  const [items, setItems] = useState<NeedsYouItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const [tasks, goals] = await Promise.all([api.tasks({ hasIt: "operator" }, 0, MOST), api.goals(NEEDS_YOU_GOALS, 0, MOST)]);
      setItems(needsYouItems(tasks.items, goals.items));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), NEEDS_YOU_POLL_MS);
    return () => clearInterval(timer);
  }, [load, refresh]);
  return { items, error, reload: () => void load() };
}
