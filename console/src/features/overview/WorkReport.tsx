// Created by Muxxy in run t-33d72d7e8075:run-1 on harness:pi-lean/claude:claude-sonnet-5-5.

import { useCallback, useEffect, useRef, useState } from "react";
import { Anchor, Button, Group, Paper, Stack, Text, TextInput, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { api, ApiError } from "../../api";
import { SubTitle, when } from "../../components/ui";
import { NeedButton } from "../goals/needActions";
import { reloadNeeds, useGoalNeeds } from "./needsYou";
import { useWorkChanged } from "../goals/goalSync";
import type { GoalAction, ReportItem, SuggestedAction, WorkAnswer, WorkReportAnswer } from "./api";

const VISIT_KEY = "mh:overview:last-visit";
// How often the open page asks for a newer report.
export const POLL_MS = 60_000;
// On a first visit, with no mark stored, the report covers the last day. The server writes a new report only
// when it is sent a since, so a first visit sends one too.
export const FIRST_LOOK_MS = 24 * 60 * 60 * 1000;

// When the operator last opened the Overview, and the mark for this visit. The first is what the report is
// asked for; the second is stored for the next visit.
function markVisit(): string {
  const now = new Date();
  let before: string | null = null;
  try {
    before = window.localStorage.getItem(VISIT_KEY);
    window.localStorage.setItem(VISIT_KEY, now.toISOString());
  } catch {
    before = null;
  }
  return before && !Number.isNaN(Date.parse(before)) ? before : new Date(now.getTime() - FIRST_LOOK_MS).toISOString();
}

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.detail : err instanceof Error ? err.message : String(err);
}

// The rows of one part of the report. A row about a goal names it as a link to its page. actions: what each
// goal needs now, by goal id; given, the first row about a goal carries its buttons, so a need is acted on
// where it is read. shown: the goals whose buttons an earlier part already carries.
function Items({ title, items, actions, shown, onDone }: { title: string; items: ReportItem[]; actions?: Record<string, GoalAction[]>; shown?: Set<string>; onDone?: () => void }) {
  if (!items.length) return null;
  return (
    <Stack gap={4}>
      <SubTitle mt={4}>{title}</SubTitle>
      {items.map((item, i) => {
        const first = !!actions && !!item.goal_id && !shown?.has(item.goal_id);
        if (first) shown?.add(item.goal_id);
        const needs = first ? actions?.[item.goal_id] ?? [] : [];
        return (
          <Stack key={i} gap={6} data-testid="report-row">
            <Text size="sm">
              {item.goal && (item.goal_id ? <><Anchor component={Link} to={`/goals/${item.goal_id}`}>{item.goal}</Anchor>: </> : item.goal + ": ")}
              {item.what ?? item.why}{item.what && item.why ? " " + item.why : ""}
            </Text>
            {first && (
              <Stack gap={6} pl="md" data-testid="report-actions" style={{ borderLeft: "2px solid var(--mantine-color-default-border)" }}>
                {/* Each thing the goal needs, on a line of its own: what it is, from the records, and the
                    button that does it, so no button is shown without what it is for. */}
                {needs.map((need, n) => (
                  <Group key={n} justify="space-between" wrap="wrap" gap="xs" data-testid="report-action">
                    <Text size="sm" c="dimmed" style={{ flex: "1 1 280px" }}>{need.what}</Text>
                    <NeedButton need={need} planning={false} goalId={item.goal_id} label={need.label || null} onDone={onDone ?? (() => undefined)} away />
                  </Group>
                ))}
                {/* Nothing its records call for now: the goal's page is where to look. */}
                {!needs.length && (
                  <Group justify="flex-end">
                    <Button size="xs" variant="default" component={Link} to={`/goals/${item.goal_id}`}>Open the goal</Button>
                  </Group>
                )}
              </Stack>
            )}
          </Stack>
        );
      })}
    </Stack>
  );
}

const OPENS = { plan: "Open the plan", goal: "Open the goal", next_steps: "See its next steps" };

// The steps an answer suggests, each with the goal it is about and the button that takes it. A step that only
// looks opens the goal's page; the others are the goal's own buttons.
function Suggested({ actions, onDone }: { actions: SuggestedAction[]; onDone: () => void }) {
  if (!actions.length) return null;
  return (
    <Stack gap={6} mt="xs" data-testid="answer-actions">
      {actions.map((step, i) => (
        <Group key={i} gap="sm" wrap="wrap" data-testid="answer-action">
          <Text size="sm" fw={600}>{step.goal}</Text>
          {step.action === "open_goal" ? (
            <Button size="xs" variant="default" component={Link} to={`/goals/${step.goal_id}`}>{OPENS[step.says ?? "goal"]}</Button>
          ) : (
            <NeedButton need={step as GoalAction} planning={false} goalId={step.goal_id} label={step.label || null} onDone={onDone} away />
          )}
        </Group>
      ))}
    </Stack>
  );
}

// The planner's latest report on all goals, with a box to ask about the work.
export function WorkReport() {
  // Read once: a return after time away sends the time of the last visit, whatever the polls after it do.
  const since = useRef<string | undefined>(undefined);
  if (since.current === undefined) since.current = markVisit();

  const [found, setFound] = useState<WorkReportAnswer | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<WorkAnswer | null>(null);
  const [askFailed, setAskFailed] = useState<string | null>(null);

  const live = useRef(true);
  const read = useCallback(
    () =>
      api.overviewReport(since.current).then(
        (got) => { if (live.current) { setFound(got); setFailed(null); } },
        (err) => { if (live.current) setFailed(errorMessage(err)); },
      ),
    [],
  );
  useEffect(() => {
    live.current = true;
    void read();
    const timer = window.setInterval(() => void read(), POLL_MS);
    return () => { live.current = false; window.clearInterval(timer); };
  }, [read]);
  // Read again the moment anything in the work changes, with the rest of the page (goals/goalSync.ts).
  useWorkChanged(() => void read());

  const ask = async () => {
    const text = question.trim();
    if (!text || asking) return;
    setAsking(true);
    setAskFailed(null);
    try {
      setAnswer(await api.overviewAsk(text, since.current));
    } catch (err) {
      setAnswer(null);
      setAskFailed(errorMessage(err));
    } finally {
      setAsking(false);
    }
  };

  const report = found?.report ?? null;
  // The goals whose buttons a row already carries, so a goal named twice gets them once.
  const withButtons = new Set<string>();
  // What each goal needs now comes from the one list every page shares (needsYou.ts), so a row's buttons
  // are the same as the Needs you list's, and change with it. After a button acted, that list is read again.
  const needsOf = useGoalNeeds();
  const reread = () => void reloadNeeds();
  const goalsNamed = [...(report?.needs_you ?? []), ...(report?.not_moving ?? [])].map((row) => row.goal_id).filter(Boolean);
  const actions: Record<string, GoalAction[]> = Object.fromEntries(goalsNamed.map((id) => [id, needsOf(id).slice(0, 3)]));
  return (
    <Paper withBorder p="md" mb="lg" data-testid="work-report">
      <Stack gap="xs">
        <Group justify="space-between" align="baseline">
          <Title order={4}>Report on all goals</Title>
          {report && <Text size="xs" c="dimmed" data-testid="work-report-written">Written {when(report.written_at)}</Text>}
        </Group>
        {failed && <Text size="sm" c="red" role="alert">Could not refresh the report: {failed}.{report ? " The last report stays." : ""}</Text>}
        {found && !report && !failed && <Text size="sm" c="dimmed">No report has been written yet.</Text>}
        {!found && !failed && <Text size="sm" c="dimmed">Loading…</Text>}
        {report && found?.stale && (
          // The goals have moved since this was written: its words are held back, and what needs the operator
          // is in the list under this, which is read from the records.
          <Text fw={600} role="status" data-testid="work-report-stale">The work changed since this report. A new one is on its way.</Text>
        )}
        {report && !found?.stale && (
          <>
            <Text fw={600}>{report.headline}</Text>
            <Items title="Needs you" items={report.needs_you} actions={actions} shown={withButtons} onDone={reread} />
            <Items title="Moving" items={report.moving} />
            {/* A goal already under Needs you is not said again here: the report's model writes both lists. */}
            <Items title="Waiting" items={report.not_moving.filter((row) => !row.goal_id || !report.needs_you.some((n) => n.goal_id === row.goal_id))} actions={actions} shown={withButtons} onDone={reread} />
            {report.spend && <Text size="sm">{report.spend}</Text>}
            {report.finished && <Text size="sm">{report.finished}</Text>}
          </>
        )}
        {found?.error && <Text size="xs" c="dimmed">A new report could not be written: {found.error}</Text>}
        {found?.note && !found.stale && <Text size="xs" c="dimmed">{found.note}</Text>}
        <form onSubmit={(e) => { e.preventDefault(); void ask(); }}>
          <Group gap="xs" align="flex-end" wrap="nowrap">
            <TextInput style={{ flex: 1 }} aria-label="Ask about the work" placeholder="Ask about the work" value={question}
              onChange={(e) => setQuestion(e.currentTarget.value)} maxLength={2000} />
            <Button type="submit" loading={asking} disabled={!question.trim()}>Ask</Button>
          </Group>
        </form>
        {answer && (
          <div data-testid="work-answer">
            <Text size="sm">{answer.answer}</Text>
            {!answer.in_record && <Text size="xs" c="dimmed">The record does not hold the answer.</Text>}
            <Suggested actions={answer.actions ?? []} onDone={reread} />
          </div>
        )}
        {askFailed && <Text size="sm" c="red" role="alert" data-testid="work-ask-error">{askFailed}</Text>}
      </Stack>
    </Paper>
  );
}
