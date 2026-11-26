import { useEffect, useRef } from "react";
import { api } from "../../api";
import { reloadNeeds } from "../overview/needsYou";
import { PLAN_CHANGED } from "./needActions";

// One signal for "this goal changed", so every part of a page that shows the goal reads it again at the same
// moment. Parts that each polled on their own clock disagreed for seconds at a time: an operator who had just
// approved a plan still read "waiting for you to approve" under a goal that was running.
//
// The signal has two sources. An operator's own action sends it as soon as the service answers (goalChanged),
// so the page does not wait to be told what it just did. And the goal's event stream sends it whenever the
// service records anything about the goal or its tasks (useGoalStream), so a change made by the planner, a
// run or another operator shows as it happens.

export const GOAL_CHANGED = "mh:goal-changed";
// Events that arrive in a burst are answered with one reload.
export const SETTLE_MS = 150;

// The same for pages that show many goals and tasks at once (the Overview, the menu's lists, a task's page):
// something in the work changed, and each reads again.
export const WORK_CHANGED = "mh:work-changed";

// Says the goal changed: every part showing it reads again, with the plan and what needs the operator.
export function goalChanged(goalId: string): void {
  window.dispatchEvent(new CustomEvent(GOAL_CHANGED, { detail: goalId }));
  window.dispatchEvent(new CustomEvent(PLAN_CHANGED));
  workChanged();
}

// Says something in the work changed, with no one goal to name: a task's status, a pull request, a budget.
export function workChanged(): void {
  window.dispatchEvent(new CustomEvent(WORK_CHANGED));
  void reloadNeeds();
}

// Calls reload whenever anything in the work changes.
export function useWorkChanged(reload: () => void): void {
  const latest = useRef(reload);
  latest.current = reload;
  useEffect(() => {
    const heard = () => latest.current();
    window.addEventListener(WORK_CHANGED, heard);
    return () => window.removeEventListener(WORK_CHANGED, heard);
  }, []);
}

// The events that change what a page says about the work. A run's own chatter (a model call, a tool call, a
// line of output) is not among them: it is followed on the task's page, by the task's own stream.
export const WORK_EVENTS = [
  "goal_created", "goal_changed", "goal_met", "goal_budget_reached", "goal_acts_as_changed", "goal_update_written",
  "plan_requested", "plan_saved", "plan_updated", "plan_approved", "plan_fix_accepted", "plan_fix_dismissed",
  "plan_review_started", "plan_review_finished", "plan_findings_settled",
  "issue_added", "issue_joined", "issue_dropped", "issue_released", "issue_closed", "issue_split", "issue_sized",
  "task_created", "task_status_changed", "task_held", "task_unblocked", "task_cancel_requested",
  "pull_request_opened", "pull_request_merged", "pull_request_closed", "pull_request_reviewed", "pull_request_checks",
  "budget_hold", "budget_decision", "review_hold", "review_proceeded",
  "operator_review_claimed", "operator_review_released", "operator_review_submitted",
];

// Where the work's events are streamed from, starting at the newest.
export function workTailUrl(base = "", ticket: string | null = null): string {
  const query = new URLSearchParams({ event_type: WORK_EVENTS.join(","), from_position: "-1" });
  if (ticket) query.set("ticket", ticket);
  return `${base}/v1/events/tail?${query.toString()}`;
}

// Calls reload whenever the goal changes.
export function useGoalChanged(goalId: string | undefined, reload: () => void): void {
  const latest = useRef(reload);
  latest.current = reload;
  useEffect(() => {
    if (!goalId) return;
    const heard = (event: Event) => {
      if ((event as CustomEvent<string>).detail === goalId) latest.current();
    };
    window.addEventListener(GOAL_CHANGED, heard);
    return () => window.removeEventListener(GOAL_CHANGED, heard);
  }, [goalId]);
}

// Where the goal's events are streamed from, starting at the newest.
export function goalTailUrl(goalId: string, base = "", ticket: string | null = null): string {
  const query = new URLSearchParams({ goal_id: goalId, from_position: "-1" });
  if (ticket) query.set("ticket", ticket);
  return `${base}/v1/events/tail?${query.toString()}`;
}

// Opens a stream of events and calls heard, once for a burst, with the goals the burst named. A stream that
// drops is opened again; while it is down, a page's own slower reads still keep it right.
function follow(url: (base: string, ticket: string | null) => string, heard: (goalIds: string[]) => void): () => void {
  let live = true;
  let stream: EventSource | null = null;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let settle: ReturnType<typeof setTimeout> | undefined;
  let goals = new Set<string>();
  const open = () =>
    api.streamTicket().then(
      ({ base, ticket }) => {
        if (!live) return;
        stream = new EventSource(url(base, ticket));
        stream.addEventListener("event", (message) => {
          try {
            const event = JSON.parse((message as MessageEvent).data ?? "{}");
            const goal = event?.tags?.goal_id ?? event?.payload?.goal_id;
            if (typeof goal === "string" && goal) goals.add(goal);
          } catch {
            // An event that cannot be read still says something changed.
          }
          clearTimeout(settle);
          settle = setTimeout(() => {
            if (!live) return;
            const named = [...goals];
            goals = new Set();
            heard(named);
          }, SETTLE_MS);
        });
        stream.onerror = () => {
          if (stream?.readyState === EventSource.CLOSED) {
            stream.close();
            retry = setTimeout(open, 2000);
          }
        };
      },
      () => {
        if (live) retry = setTimeout(open, 5000);
      },
    );
  void open();
  return () => {
    live = false;
    clearTimeout(retry);
    clearTimeout(settle);
    stream?.close();
  };
}

// Follows everything that changes the work, for the whole console, while someone is signed in: one stream,
// opened by the app's frame. Each burst says the work changed, and that a goal changed for each it named.
export function useWorkStream(on: boolean): void {
  useEffect(() => {
    if (!on || typeof EventSource === "undefined") return;
    return follow(workTailUrl, (goals) => {
      for (const goal of goals) window.dispatchEvent(new CustomEvent(GOAL_CHANGED, { detail: goal }));
      if (goals.length) window.dispatchEvent(new CustomEvent(PLAN_CHANGED));
      workChanged();
    });
  }, [on]);
}

// Follows the goal's events while its page is open, and says the goal changed at each. It hears what the
// work's stream does not: a run's own events, which name their task and not its goal.
export function useGoalStream(goalId: string | undefined): void {
  useEffect(() => {
    if (!goalId || typeof EventSource === "undefined") return;
    return follow((base, ticket) => goalTailUrl(goalId, base, ticket), () => goalChanged(goalId));
  }, [goalId]);
}
