import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Anchor, Button, Group, Stack, Text } from "@mantine/core";
import { IconArrowBackUp, IconExternalLink, IconGitMerge, IconX } from "@tabler/icons-react";
import { api, type Task, type TaskStanding } from "../../api";
import { ConfirmButton } from "../../components/ConfirmButton";
import { VoicePanel } from "../../components/Voice";
import { when } from "../../components/ui";
import { usd } from "../../money";
import { retryLimit } from "./Remedies";

// The Task Shepherd, at the head of a task's page: who has the task, what the Shepherd saw, and for a task
// that waits on a person, the moves it offers as buttons. The moves and their order are the supervisor's
// (supervisor/moves.py), worked out from how the pull request really stands. The first is the recommended one.

const sentence = (text: string) => `${text.charAt(0).toUpperCase()}${text.slice(1)}${/[.!?]$/.test(text) ? "" : "."}`;

// What the Shepherd says first, by who has the task.
const OPENING: Record<TaskStanding["owner"], string> = {
  operator: "I need you on this one.",
  supervisor: "I have this one.",
  workflow: "Nothing for you to do: the task is running.",
  scheduler: "Nothing for you to do: the task waits its turn.",
  planner: "Nothing for you to do: the Goal Planner has it.",
};

// A task nobody has: it was canceled, or its goal was. Its record names the planner, whose sweep closes what
// is left of a goal, and saying the Goal Planner "has it" would read as work still going on.
const OVER = "Nothing for you to do: this task is over.";

interface Move {
  label: string;
  icon?: ReactNode;
  // Asked before the move runs: the dialog's title and what will happen.
  asks?: { title: string; says: string };
  run?: (task: Task) => Promise<unknown>;
  // A move that opens a page instead of doing something here.
  to?: (task: Task) => { href: string; outside?: boolean } | null;
}

function movesFor(task: Task): Record<string, Move> {
  const limit = usd(retryLimit(task, false));
  return {
    merge: {
      label: "Merge",
      icon: <IconGitMerge size={14} />,
      asks: { title: "Merge the pull request?", says: "Its review is clean. It merges once its checks pass on the newest base." },
      run: (t) => api.mergeTask(t.id),
    },
    resolve_and_merge: {
      label: "Resolve the conflict and merge",
      icon: <IconGitMerge size={14} />,
      asks: {
        title: "Resolve the conflict and merge?",
        says: `A fix run resolves the conflict with the base first. Then its checks and a review run, and it merges once the review is clean. The task can spend up to ${limit}.`,
      },
      run: (t) => api.mergeTask(t.id),
    },
    retry_stronger: { label: "Retry with a stronger model", icon: <IconArrowBackUp size={14} />, run: (t) => api.retryTask(t.id, retryLimit(t, false), true) },
    retry: { label: "Retry", icon: <IconArrowBackUp size={14} />, run: (t) => api.retryTask(t.id, retryLimit(t, false)) },
    merge_as_is: {
      label: "Merge as it is",
      icon: <IconGitMerge size={14} />,
      asks: { title: "Merge as it is?", says: "The last review's findings stay unfixed, and the pull request merges once its checks pass." },
      run: (t) => api.mergeTask(t.id),
    },
    start_over: {
      label: "Start over on main",
      icon: <IconArrowBackUp size={14} />,
      asks: {
        title: "Start over on main?",
        says: `The pull request is closed and its branch deleted. The task then runs fresh from the current base and opens a new pull request. It can spend up to ${limit}.`,
      },
      run: (t) => api.restartTask(t.id, retryLimit(t, false)),
    },
    cancel: {
      label: "Cancel the task",
      icon: <IconX size={14} />,
      asks: { title: "Cancel the task?", says: "The task ends and no more runs start for it. Its pull request stays open on the code host." },
      run: (t) => api.cancelTask(t.id),
    },
    open_pull_request: {
      label: "Open the pull request",
      icon: <IconExternalLink size={14} />,
      to: (t) => (t.pr_url && /^https?:/.test(t.pr_url) ? { href: t.pr_url, outside: true } : null),
    },
    open_goal: { label: "Open the goal", to: (t) => (t.goal_id ? { href: `/goals/${t.goal_id}` } : null) },
  };
}

function Moves({ task, moves, onChanged }: { task: Task; moves: string[]; onChanged?: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const known = movesFor(task);
  const offered = moves.filter((id) => known[id] && (!known[id].to || known[id].to!(task)));
  if (!offered.length) return null;

  async function go(id: string) {
    setBusy(id);
    setError(null);
    try {
      await known[id].run!(task);
      setDone(id);
    } catch (err) {
      setError(`${known[id].label} did not go through: ${err instanceof Error ? err.message : String(err)}. Nothing changed. Try it again.`);
      throw err;
    } finally {
      setBusy(null);
      onChanged?.();
    }
  }

  const spends = offered.some((id) => id === "retry" || id === "retry_stronger");
  return (
    <Stack gap={6} mt="xs">
      <Group gap="sm" data-testid="shepherd-moves">
        {offered.map((id, n) => {
          const move = known[id];
          const first = n === 0;
          const label = `${move.label}${first ? " (recommended)" : ""}`;
          const variant = first ? "filled" : "default";
          const to = move.to?.(task);
          if (to) {
            return to.outside
              ? <Button key={id} component="a" href={to.href} target="_blank" rel="noreferrer" size="compact-sm" variant={variant} leftSection={move.icon}>{label}</Button>
              : <Button key={id} component={Link} to={to.href} size="compact-sm" variant={variant} leftSection={move.icon}>{label}</Button>;
          }
          if (move.asks) {
            return (
              <ConfirmButton
                key={id}
                size="compact-sm"
                variant={variant}
                color={id === "cancel" ? "red" : undefined}
                loading={busy === id}
                disabled={!!done}
                leftSection={move.icon}
                label={done === id ? "Asked for" : label}
                title={move.asks.title}
                says={move.asks.says}
                confirm={move.label}
                onConfirm={() => go(id)}
              />
            );
          }
          return (
            <Button key={id} size="compact-sm" variant={variant} loading={busy === id} disabled={!!done} leftSection={move.icon} onClick={() => void go(id).catch(() => undefined)}>
              {done === id ? "Sent back to the queue" : label}
            </Button>
          );
        })}
      </Group>
      {spends && !done && <Text size="xs" c="dimmed">A retry can spend up to {usd(retryLimit(task, false))}.</Text>}
      {error && <Text size="xs" c="red">{error}</Text>}
    </Stack>
  );
}

export function ShepherdPanel({ task, status, children, onChanged }: {
  task: Task;
  status: string;
  // What the task's failure says in full, shown under the Shepherd's words.
  children?: ReactNode;
  onChanged?: () => void;
}) {
  const standing = task.standing;
  if (!standing || status === "closed") return children ? <>{children}</> : null;
  const mine = standing.owner === "supervisor";
  const yours = standing.owner === "operator";
  const moves = yours ? standing.moves ?? [] : [];
  return (
    <VoicePanel who="shepherd" note={`looked ${when(standing.checked_at)}`} testId="shepherd-panel">
      <Text fw={650} size={yours || mine ? "lg" : "sm"} lh={1.3}>{standing.over ? OVER : OPENING[standing.owner] ?? ""}</Text>
      <Text size="sm" mt={2}>
        {sentence(standing.owner === "workflow" && task.status_reason ? task.status_reason : standing.reason)}
        {standing.saw ? ` ${sentence(standing.saw)}` : ""}
        {mine && standing.attempts > 0 && ` I have acted on it ${standing.attempts} ${standing.attempts === 1 ? "time" : "times"}.`}
        {mine && " I look again within a minute."}
        {mine && standing.last_error && ` My last try failed: ${standing.last_error}`}
      </Text>
      {yours && !moves.length && standing.ask && <Text size="sm" mt={2}><Text span fw={600}>What to do:</Text> {standing.ask}</Text>}
      {yours && <Moves task={task} moves={moves} onChanged={onChanged} />}
      {(yours || mine) && <Text size="xs" c="dimmed" mt={6}>It has been this way since {when(standing.since)}. <Anchor component={Link} to="/help#stuck-tasks" size="xs">How I decide</Anchor></Text>}
      {children && <div style={{ marginTop: 12 }}>{children}</div>}
    </VoicePanel>
  );
}
