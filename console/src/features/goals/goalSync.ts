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

// Says the goal changed: every part showing it reads again, with the plan and what needs the operator.
export function goalChanged(goalId: string): void {
  window.dispatchEvent(new CustomEvent(GOAL_CHANGED, { detail: goalId }));
  window.dispatchEvent(new CustomEvent(PLAN_CHANGED));
  void reloadNeeds();
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

// Follows the goal's events while the page is open, and says the goal changed at each. A stream that drops
// is opened again; while it is down, the page's own slower reads still keep it right.
export function useGoalStream(goalId: string | undefined): void {
  useEffect(() => {
    if (!goalId || typeof EventSource === "undefined") return;
    let live = true;
    let stream: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let settle: ReturnType<typeof setTimeout> | undefined;
    const open = () =>
      api.streamTicket().then(
        ({ base, ticket }) => {
          if (!live) return;
          stream = new EventSource(goalTailUrl(goalId, base, ticket));
          stream.addEventListener("event", () => {
            clearTimeout(settle);
            settle = setTimeout(() => live && goalChanged(goalId), SETTLE_MS);
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
  }, [goalId]);
}
