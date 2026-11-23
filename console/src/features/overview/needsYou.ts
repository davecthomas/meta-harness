import { useEffect, useSyncExternalStore } from "react";
import { api, type Goal, type Task } from "../../api";
import { call } from "../../client";
import type { Need } from "../goals/needActions";
import { diagnose, oneLine } from "../tasks/diagnosis";

// What needs the operator, as one list with one count, decided in one place: the planner's GET /v1/needs
// (planner/needs.py). The Overview's list and tile, the header's inbox and the buttons on the report's rows
// all read the copy kept here, so they never disagree and they change together. Nothing in the console
// decides whether something needs the operator, or what is asked: it draws what the list says. The tasks
// and goals are read beside it only to draw more about an item (a failure's diagnosis, a budget's figures).
//
// What is not in it, on purpose: an agent's review in progress, which the operator may join but need not; and
// the kill switch, which has its own banner on every page.

// One thing that needs the operator, as the planner serves it.
export interface ServerNeed extends Need {
  key: string;
  // What it asks, in a word or three; what it is about; and one sentence on why.
  asks: string;
  title: string;
  since: string;
  goal_id: string;
  goal: string;
  label: string;
}

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
  // The need as served: its action is the button a page offers for it.
  need: ServerNeed;
}

// The goals read beside the list, to draw more about an item, and how often the list is read again.
export const NEEDS_YOU_GOALS = ["planning", "active", "budget_exhausted", "stalled"];
export const NEEDS_YOU_POLL_MS = 30_000;
const MOST = 200;

// One need as a row: named by what it asks, with where to go for it. tasks and goals only add to what is
// drawn; an item is in the list because the planner's list holds it, whether or not they were read.
function item(need: ServerNeed, tasks: Map<string, Task>, goals: Map<string, Goal>): NeedsYouItem {
  const task = need.task_id ? tasks.get(need.task_id) : undefined;
  const goal = need.goal_id ? goals.get(need.goal_id) : undefined;
  // A failure reads better by its diagnosis, when the console has one for its reason.
  const diagnosis = task && ["failed", "review_unresolved"].includes(task.status) ? diagnose({ status: task.status, reason: task.status_reason }) : null;
  const pr = need.action === "merge" && need.pr_url && /^https?:/.test(need.pr_url) ? need.pr_url : undefined;
  return {
    // The keys a row has always had: a task's, and a goal's budget's.
    key: need.task_id ? `task-${need.task_id}` : need.action === "raise_budget" ? `goal-${need.goal_id}` : need.key,
    action: need.asks,
    title: need.title || need.goal,
    to: need.task_id ? `/tasks/${need.task_id}` : `/goals/${need.goal_id}`,
    detail: diagnosis ? oneLine(diagnosis) : need.what,
    href: pr,
    hrefLabel: pr ? "Open the pull request" : undefined,
    task,
    goal,
    since: need.since || undefined,
    need,
  };
}

// The one list, as rows. needs: the planner's; tasks and goals: read beside it, for what each row draws.
export function needsYouItems(needs: ServerNeed[], tasks: Task[] = [], goals: Goal[] = []): NeedsYouItem[] {
  const byTask = new Map(tasks.map((t) => [t.id, t]));
  const byGoal = new Map(goals.map((g) => [g.id, g]));
  return needs.map((need) => item(need, byTask, byGoal));
}

// The line under the count: what it is made of, by what each asks for, so it is never a bare number.
export function needsYouNote(items: NeedsYouItem[] | null): string {
  if (items === null) return "";
  if (!items.length) return "Nothing needs you.";
  const counts = new Map<string, number>();
  for (const item of items) counts.set(item.action, (counts.get(item.action) ?? 0) + 1);
  return `${[...counts].map(([action, n]) => `${n} to ${action.charAt(0).toLowerCase()}${action.slice(1)}`).join(", ")}.`;
}

// The one copy of the list every page shares: read once, kept here, and told to whoever is showing it.
type Held = { items: NeedsYouItem[] | null; error: string | null };
let held: Held = { items: null, error: null };
const watching = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | undefined;
let reading: Promise<void> | null = null;

async function read(): Promise<void> {
  try {
    const [found, tasks, goals] = await Promise.all([
      call<{ needs: ServerNeed[] }>("/v1/needs"),
      // Only for what a row draws: a failed read of either leaves the rows plainer, not missing.
      api.tasks({ hasIt: "operator" }, 0, MOST).then((page) => page.items, () => [] as Task[]),
      api.goals(NEEDS_YOU_GOALS, 0, MOST).then((page) => page.items, () => [] as Goal[]),
    ]);
    held = { items: needsYouItems(found.needs, tasks, goals), error: null };
  } catch (err) {
    // The list it had stays, with why it could not be read: an empty list must mean nothing needs the operator.
    held = { ...held, error: err instanceof Error ? err.message : String(err) };
  }
  for (const tell of watching) tell();
}

// Reads the list again now, for every page showing it. Called after anything the operator does that can
// change what needs them, so each place that shows it changes at once and together.
export function reloadNeeds(): Promise<void> {
  reading ??= read().finally(() => {
    reading = null;
  });
  return reading;
}

function watch(tell: () => void): () => void {
  watching.add(tell);
  if (watching.size === 1) {
    void reloadNeeds();
    timer = setInterval(() => void reloadNeeds(), NEEDS_YOU_POLL_MS);
  }
  return () => {
    watching.delete(tell);
    if (!watching.size) {
      clearInterval(timer);
      timer = undefined;
    }
  };
}

// Forgets the list: for tests, so one test's list is not the next one's.
export function forgetNeeds(): void {
  held = { items: null, error: null };
}

// The list as it is now: the one shared copy, read again every NEEDS_YOU_POLL_MS and whenever `refresh`
// changes. A read that fails keeps the list it had and says why.
export function useNeedsYou(refresh = 0): { items: NeedsYouItem[] | null; error: string | null; reload: () => void } {
  const now = useSyncExternalStore(watch, () => held);
  useEffect(() => {
    if (refresh) void reloadNeeds();
  }, [refresh]);
  return { items: now.items, error: now.error, reload: () => void reloadNeeds() };
}

// What one goal needs now, from the shared list: the buttons for a row about it, wherever the row is.
export function useGoalNeeds(): (goalId: string) => ServerNeed[] {
  const { items } = useNeedsYou();
  return (goalId) => (items ?? []).filter((i) => i.need.goal_id === goalId).map((i) => i.need);
}
