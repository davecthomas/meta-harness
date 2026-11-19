import { useEffect, useRef, useState } from "react";
import type { Task } from "../../api";

// An answer to a budget hold is queued for the task's workflow and handed over at the scheduler's next pass, so
// for a few seconds after it is accepted the task still reads as held, with its old reason. Until the workflow
// has taken the answer, the task's page says what was sent in place of the hold.
//
// The workflow writes the task once it has the answer: it waits no more, or it is held again with what is short
// now (the RunTask workflow's budget-hold-again patch), which moves the task's updated_at. So the line stays
// until the task reads as written after the answer was accepted, however long the scheduler takes; a read from
// before the answer, arriving late, leaves it. A workflow
// from before that patch writes nothing after an answer that was not enough: its line stays until the page is
// reloaded.

// How often the task is read again while its answer is on its way: the scheduler's pass is a few seconds.
export const REREAD_MS = 3_000;

export interface Sent {
  // What was sent and what happens next, as the page says it.
  says: string;
  // The task's updated_at when the answer was accepted; a later write is the workflow's, with the answer taken.
  at: string;
}

const held = (task: Task) => task.status === "running" && task.waiting_on === "budget";

// How two of the task's updated_at compare: below 0 when a is the earlier. A read that was on its way when the
// answer was sent can arrive after it, with the task as it was before: only a later write ends the wait.
export function compareAt(a: string, b: string): number {
  const ta = Date.parse(a);
  const tb = Date.parse(b);
  // Within the same millisecond, the service's own strings (to the microsecond) still order.
  if (!Number.isNaN(ta) && !Number.isNaN(tb) && ta !== tb) return ta - tb;
  return a === b ? 0 : a < b ? -1 : 1;
}

// The answer sent to a task held for budget, while the workflow has yet to take it, and send to record one.
// reread reads the task again; the page's copy of the task is what ends the wait.
export function useBudgetAnswer(task: Task, reread: () => void) {
  const [sent, setSent] = useState<Sent | null>(null);
  const rereadRef = useRef(reread);
  rereadRef.current = reread;
  // The workflow has taken the answer: the task waits no more, or it was held again, for what is short now.
  // A read older than the answer says nothing about it, held or not.
  const taken =
    sent !== null && (compareAt(task.updated_at, sent.at) > 0 || (!held(task) && compareAt(task.updated_at, sent.at) === 0));
  useEffect(() => {
    if (taken) setSent(null);
  }, [taken]);
  useEffect(() => {
    if (!sent || taken) return;
    const every = setInterval(() => rereadRef.current(), REREAD_MS);
    return () => clearInterval(every);
  }, [sent, taken]);
  return {
    sent: taken ? null : sent,
    // at: the task's updated_at as the answer left it.
    send: (says: string, at: string) => setSent({ says, at }),
  };
}
