import { useEffect, useState } from "react";
import { Anchor, Badge, Box, Button, Group, Paper, Stack, Text, Tooltip, UnstyledButton } from "@mantine/core";
import {
  IconArrowBackUp, IconChecks, IconCircleCheck, IconClock, IconEye, IconFlag, IconGitMerge, IconGitPullRequest, IconNote, IconPlayerPlay,
} from "@tabler/icons-react";
import type { MhEvent } from "../../api";
import { usd } from "../../money";
import { when } from "../../components/ui";
import { Timeline } from "./Timeline";
import { COLUMNS, candidateName, rows, span, steps, took, totals, type Step, type StepKind, type Tone } from "./path";

// The picture's measures, in pixels: a stage's column, the gap between two, and the strip a loop back is drawn in.
const WIDE = 184;
const GAP = 16;
const BACK = 34;
const LINE = "var(--mantine-color-default-border)";
const middle = (column: number) => column * (WIDE + GAP) + WIDE / 2;

const ICONS: Record<StepKind, typeof IconClock> = {
  wait: IconClock, run: IconPlayerPlay, review: IconEye, pull_request: IconGitPullRequest, checks: IconChecks,
  merged: IconGitMerge, merge_checks: IconCircleCheck, parked: IconClock, end: IconFlag,
};
const COLORS: Record<Tone, string> = { ok: "teal", bad: "red", warn: "yellow", plain: "gray" };

// A step's numbers on one line: how long, what it cost, how many turns.
export function numbers(step: Step, now: number): string {
  const parts: string[] = [];
  if (step.start !== step.end) parts.push(`${span(took(step, now))}${step.end ? "" : " so far"}`);
  if (step.usd !== undefined) parts.push(usd(step.usd));
  if (step.turns) parts.push(`${step.turns} turns`);
  return parts.join(" · ");
}

// Everything known about a step, for the hover.
function About({ step, now }: { step: Step; now: number }) {
  return (
    <Stack gap={2} maw={360}>
      <Text size="xs" fw={600}>{step.title}</Text>
      {step.detail && <Text size="xs">{step.detail}</Text>}
      {step.candidate && <Text size="xs">{step.candidate}</Text>}
      {step.runId && <Text size="xs" ff="monospace">{step.runId}</Text>}
      <Text size="xs">{when(step.start)}{step.end && step.end !== step.start ? ` to ${when(step.end)}` : ""}</Text>
      {numbers(step, now) && <Text size="xs">{numbers(step, now)}</Text>}
      {step.notes.map((n, i) => <Text key={i} size="xs">{n}</Text>)}
    </Stack>
  );
}

function StepCard({ step, now, picked, onPick }: { step: Step; now: number; picked: boolean; onPick: () => void }) {
  const Icon = ICONS[step.kind];
  const color = COLORS[step.tone];
  const waiting = step.kind === "wait" || step.kind === "parked";
  return (
    <Tooltip label={<About step={step} now={now} />} multiline withArrow openDelay={250} position="bottom">
      <UnstyledButton onClick={onPick} aria-pressed={picked} data-testid={`step-${step.key}`} w="100%">
        <Paper
          p={8}
          withBorder
          style={{
            borderStyle: waiting ? "dashed" : "solid",
            borderColor: picked || step.now ? `var(--mantine-color-${step.now ? "blue" : "gray"}-6)` : undefined,
            boxShadow: step.now ? "0 0 0 2px var(--mantine-color-blue-light)" : undefined,
          }}
        >
          <Stack gap={2}>
            <Group gap={6} wrap="nowrap" align="flex-start">
              <Icon size={15} color={`var(--mantine-color-${color}-6)`} style={{ flexShrink: 0, marginTop: 2 }} />
              <Text size="sm" fw={600} lh={1.25} style={{ flex: 1 }}>{step.title}</Text>
              {step.now && <Badge size="xs" color="blue" variant="filled" style={{ flexShrink: 0 }}>Now</Badge>}
              {step.chosen && <Badge size="xs" color="teal" variant="light" style={{ flexShrink: 0 }}>Chosen</Badge>}
            </Group>
            {step.detail && <Text size="xs" c={step.tone === "bad" ? "red" : "dimmed"} lineClamp={2}>{step.detail}</Text>}
            {step.candidate && <Text size="xs" c="dimmed" truncate>{candidateName(step.candidate)}</Text>}
            {numbers(step, now) && <Text size="xs" ff="monospace">{numbers(step, now)}</Text>}
            {step.notes.map((n, i) => (
              <Group key={i} gap={4} wrap="nowrap" align="flex-start">
                <IconNote size={12} style={{ flexShrink: 0, marginTop: 3 }} />
                <Text size="xs" lineClamp={2}>{n}</Text>
              </Group>
            ))}
          </Stack>
        </Paper>
      </UnstyledButton>
    </Tooltip>
  );
}

// What a picked step opens: its run's terminal, its review round, its checks or its pull request, and
// under them the run's turns one by one.
function Picked({ step, events, onOpenRun, onOpenReviews }: { step: Step; events: MhEvent[]; onOpenRun: (runId: string) => void; onOpenReviews: () => void }) {
  return (
    <Stack gap="sm" mt="md" data-testid="picked">
      <Group gap="xs">
        <Text size="sm" fw={600}>{step.title}</Text>
        {step.runId && <Button size="compact-xs" variant="light" onClick={() => onOpenRun(step.runId!)}>Open its terminal</Button>}
        {step.kind === "review" && <Button size="compact-xs" variant="light" onClick={onOpenReviews}>Open its review round</Button>}
        {step.url && <Anchor href={step.url} target="_blank" rel="noreferrer" size="sm">{step.kind === "checks" ? "Open its checks" : "Open the pull request"}</Anchor>}
      </Group>
      {step.detail && <Text size="sm" c={step.tone === "bad" ? "red" : undefined}>{step.detail}</Text>}
      {step.notes.map((n, i) => <Text key={i} size="sm">{n}</Text>)}
      {step.runId && <Timeline events={events.filter((e) => e.run_id === step.runId || e.payload?.run_id === step.runId)} />}
    </Stack>
  );
}

// A task's path as a picture. Stages are columns and time runs left to right; each time the task goes
// back to an earlier stage (a review's findings to a fix run, a retry, a merge the supervisor asked for
// again) it starts a new row, so a loop shows as a return, not as a longer line.
export function TaskPath(props: { events: MhEvent[]; pick?: string | null; onOpenRun: (runId: string) => void; onOpenReviews: () => void; onPickRun?: (runId: string) => void }) {
  const path = steps(props.events);
  // A link that names a run opens on that run's step.
  const [picked, setPicked] = useState<string | null>(props.pick ?? null);
  // Steps still going show their time so far, so the picture is read again as time passes.
  const [now, setNow] = useState(() => Date.now());
  const going = path.some((s) => !s.end);
  useEffect(() => {
    if (!going) return;
    const timer = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(timer);
  }, [going]);
  if (!path.length) return <Text size="sm" c="dimmed">Nothing recorded yet.</Text>;
  const sum = totals(path, now);
  const chosen = path.find((s) => s.key === picked) ?? null;
  const all = rows(path);
  const width = COLUMNS.length * WIDE + (COLUMNS.length - 1) * GAP;
  return (
    <div data-testid="task-path">
      <Text size="sm" c="dimmed" mb="sm" data-testid="path-totals">
        {span(sum.elapsed)} from start to {path.at(-1)!.now ? "now" : "end"}, {span(sum.waited)} of it waiting
        {" · "}{sum.runs} run{sum.runs === 1 ? "" : "s"}{sum.rounds ? ` · ${sum.rounds} review round${sum.rounds === 1 ? "" : "s"}` : ""} · {usd(sum.usd)}
      </Text>
      <div style={{ overflowX: "auto", paddingBottom: 8 }}>
        <div style={{ width, minWidth: width }}>
          <div style={{ display: "grid", gridTemplateColumns: `repeat(${COLUMNS.length}, ${WIDE}px)`, columnGap: GAP, marginBottom: 8 }}>
            {COLUMNS.map((c) => <Text key={c} size="xs" fw={700} c="dimmed" tt="uppercase" ta="center">{c}</Text>)}
          </div>
          {all.map((row, r) => {
            const first = row.steps[0].column;
            const last = row.steps.at(-1)!.column;
            return (
              <div key={row.steps[0].key} data-testid={`path-row-${r}`}>
                {r > 0 && <Back from={all[r - 1].steps.at(-1)!.column} to={first} label={row.back} />}
                <div style={{ position: "relative" }}>
                  {/* The line the row's steps sit on, from its first stage to its last. */}
                  <div style={{ position: "absolute", top: 18, left: middle(first), width: middle(last) - middle(first), borderTop: `2px solid ${LINE}` }} />
                  {/* And down from its last step to the return below it, when the task came back again. */}
                  {r < all.length - 1 && <div style={{ position: "absolute", top: 18, bottom: 0, left: middle(last) - 2, borderLeft: `2px solid ${LINE}` }} />}
                  <div style={{ display: "grid", gridTemplateColumns: `repeat(${COLUMNS.length}, ${WIDE}px)`, columnGap: GAP, position: "relative", alignItems: "start" }}>
                    {COLUMNS.map((_, column) => (
                      <Stack key={column} gap={6}>
                        {row.steps.filter((s) => s.column === column).map((s) => (
                          <StepCard
                            key={s.key}
                            step={s}
                            now={now}
                            picked={picked === s.key}
                            onPick={() => {
                              setPicked(picked === s.key ? null : s.key);
                              if (s.runId && picked !== s.key) props.onPickRun?.(s.runId);
                            }}
                          />
                        ))}
                      </Stack>
                    ))}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
      {chosen && <Picked step={chosen} events={props.events} onOpenRun={props.onOpenRun} onOpenReviews={props.onOpenReviews} />}
    </div>
  );
}

// The return from one row's last stage to the next row's first: down, back to the left, and down again.
function Back({ from, to, label }: { from: number; to: number; label: string }) {
  const half = BACK / 2;
  return (
    <Box style={{ position: "relative", height: BACK }} data-testid="path-back">
      <div style={{ position: "absolute", top: 0, left: middle(to), width: middle(from) - middle(to), height: half, borderRight: `2px solid ${LINE}`, borderBottom: `2px solid ${LINE}` }} />
      <div style={{ position: "absolute", top: half, left: middle(to), height: half, borderLeft: `2px solid ${LINE}` }} />
      <Group gap={4} wrap="nowrap" style={{ position: "absolute", top: half - 11, left: middle(to) + 14, background: "var(--mantine-color-body)", padding: "0 6px" }}>
        <IconArrowBackUp size={13} color="var(--mantine-color-dimmed)" />
        <Text size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>{label}</Text>
      </Group>
    </Box>
  );
}
