import { useEffect, useRef, useState } from "react";
import type { Task } from "../../api";

// An answer to a budget hold is queued for the task's workflow and handed over at the scheduler's next pass, so
// for a few seconds after it is accepted the task still reads as held, with its old reason. Until the workflow
// has looked again, the task's page says what was sent in place of the hold.

// How often the task is read again while its answer is on its way: the scheduler's pass is a few seconds.
export const REREAD_MS = 3_000;
// How long the workflow has to look again. A task still held after this, with the reason it had when the answer
// was sent, found the answer not enough: the hold shows again.
export const LOOKED_AGAIN_MS = 30_000;

export interface Sent {
  // What was sent and what happens next, as the page says it.
  says: string;
  // The task's status reason when the answer was accepted; a new one means the workflow has looked again.
  reason: string | null;
}

const held = (task: Task) => task.status === "running" && task.waiting_on === "budget";

// The answer sent to a task held for budget, while the workflow has yet to look at it, and send to record one.
// reread reads the task again; the page's copy of the task is what ends the wait.
export function useBudgetAnswer(task: Task, reread: () => void) {
  const [sent, setSent] = useState<Sent | null>(null);
  const rereadRef = useRef(reread);
  rereadRef.current = reread;
  // The workflow has looked again: the task waits no more, or waits for a new reason.
  const looked = sent !== null && (!held(task) || (task.status_reason ?? null) !== sent.reason);
  useEffect(() => {
    if (looked) setSent(null);
  }, [looked]);
  useEffect(() => {
    if (!sent || looked) return;
    const every = setInterval(() => rereadRef.current(), REREAD_MS);
    // Still held as before once the workflow has had time to look: the answer was not enough.
    const done = setTimeout(() => {
      setSent(null);
      rereadRef.current();
    }, LOOKED_AGAIN_MS);
    return () => {
      clearInterval(every);
      clearTimeout(done);
    };
  }, [sent, looked]);
  return {
    sent: looked ? null : sent,
    send: (says: string, reason: string | null | undefined) => setSent({ says, reason: reason ?? null }),
  };
}
