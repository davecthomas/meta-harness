import type { ComponentType } from "react";
import {
  IconAlertCircle,
  IconAlertTriangle,
  IconCircle,
  IconCircleCheck,
  IconCircleDashed,
  IconCircleOff,
  IconCircleX,
  IconCoin,
  IconEye,
  IconGitMerge,
  IconHandStop,
  IconHourglass,
  IconLoader2,
  IconLock,
  IconPencil,
  IconPlayerPause,
  IconPlayerPlay,
  IconUser,
} from "@tabler/icons-react";
import { statusWords } from "./status";

// A status as a small icon in its color: the shape says what is happening (working, waiting, done, needs you),
// so it reads without telling colors apart. Hovering it says the status in words.

type Icon = ComponentType<{ size?: number; stroke?: number; className?: string; color?: string }>;

const ICONS: Record<string, Icon> = {
  // A task.
  queued: IconHourglass,
  blocked: IconLock,
  running: IconLoader2,
  in_review: IconEye,
  taken_over: IconUser,
  ready: IconGitMerge,
  review_unresolved: IconHandStop,
  closed: IconCircleCheck,
  failed: IconCircleX,
  canceled: IconCircleOff,
  refused: IconAlertCircle,
  // A goal.
  planning: IconPencil,
  active: IconPlayerPlay,
  paused: IconPlayerPause,
  not_started: IconCircleDashed,
  stalled: IconAlertTriangle,
  budget_exhausted: IconCoin,
  met: IconCircleCheck,
};

// Statuses whose icon turns: work is going on now.
const MOVING = new Set(["running"]);

export function StatusIcon({ status, size = 14 }: { status: string; size?: number }) {
  const words = statusWords(status);
  const Shape = ICONS[status] ?? IconCircle;
  return (
    <span
      role="img"
      aria-label={words.label}
      title={words.meaning ? `${words.label}: ${words.meaning}` : words.label}
      data-status={status}
      style={{ display: "inline-flex", flex: "none", color: `var(--mantine-color-${words.color}-6)` }}
    >
      <Shape size={size} stroke={2} className={MOVING.has(status) ? "mh-spin" : undefined} />
    </span>
  );
}
