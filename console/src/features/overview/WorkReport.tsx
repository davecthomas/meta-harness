// Created by Octofusion in run t-33d72d7e8075:run-1 on harness:pi-lean/claude:claude-sonnet-5-5.

import { useEffect, useRef, useState } from "react";
import { Anchor, Button, Group, Paper, Stack, Text, TextInput } from "@mantine/core";
import { Link } from "react-router-dom";
import { api, ApiError } from "../../api";
import { when } from "../../components/ui";
import { NeedButton } from "../goals/needActions";
import type { GoalAction, ReportItem, WorkAnswer, WorkReportAnswer } from "./api";

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
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{title}</Text>
      {items.map((item, i) => {
        const first = !!actions && !!item.goal_id && !shown?.has(item.goal_id);
        if (first) shown?.add(item.goal_id);
        const needs = first ? actions?.[item.goal_id] ?? [] : [];
        return (
          <Group key={i} justify="space-between" wrap="wrap" gap="xs" data-testid="report-row">
            <Text size="sm" style={{ flex: "1 1 320px" }}>
              {item.goal && (item.goal_id ? <><Anchor component={Link} to={`/goals/${item.goal_id}`}>{item.goal}</Anchor>: </> : item.goal + ": ")}
              {item.what ?? item.why}{item.what && item.why ? " " + item.why : ""}
            </Text>
            {first && (
              <Group gap="xs" wrap="wrap" data-testid="report-actions">
                {needs.map((need, n) => (
                  <NeedButton key={n} need={need} planning={false} goalId={item.goal_id} label={need.label || null} onDone={onDone ?? (() => undefined)} away />
                ))}
                {/* Nothing its records call for now: the goal's page is where to look. */}
                {!needs.length && <Button size="xs" variant="subtle" component={Link} to={`/goals/${item.goal_id}`}>Open the goal</Button>}
              </Group>
            )}
          </Group>
        );
      })}
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

  useEffect(() => {
    let live = true;
    const read = () =>
      api.overviewReport(since.current).then(
        (got) => { if (live) { setFound(got); setFailed(null); } },
        (err) => { if (live) setFailed(errorMessage(err)); },
      );
    read();
    const timer = window.setInterval(read, POLL_MS);
    return () => { live = false; window.clearInterval(timer); };
  }, []);

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
  // After a button acted, the report is read again: what the goal needs has changed.
  const reread = () => void api.overviewReport().then((got) => setFound((now) => (now ? { ...now, actions: got.actions } : got)), () => undefined);
  return (
    <Paper withBorder p="md" mb="lg" data-testid="work-report">
      <Stack gap="xs">
        <Group justify="space-between" align="baseline">
          <Text size="xs" c="dimmed" tt="uppercase" fw={600}>Report on all goals</Text>
          {report && <Text size="xs" c="dimmed" data-testid="work-report-written">Written {when(report.written_at)}</Text>}
        </Group>
        {failed && <Text size="sm" c="red" role="alert">Could not refresh the report: {failed}.{report ? " The last report stays." : ""}</Text>}
        {found && !report && !failed && <Text size="sm" c="dimmed">No report has been written yet.</Text>}
        {!found && !failed && <Text size="sm" c="dimmed">Loading…</Text>}
        {report && (
          <>
            <Text fw={600}>{report.headline}</Text>
            <Items title="Needs you" items={report.needs_you} actions={found?.actions ?? {}} shown={withButtons} onDone={reread} />
            <Items title="Moving" items={report.moving} />
            <Items title="Not moving" items={report.not_moving} actions={found?.actions ?? {}} shown={withButtons} onDone={reread} />
            {report.spend && <Text size="sm">{report.spend}</Text>}
            {report.finished && <Text size="sm">{report.finished}</Text>}
          </>
        )}
        {found?.error && <Text size="xs" c="dimmed">A new report could not be written: {found.error}</Text>}
        {found?.note && <Text size="xs" c="dimmed">{found.note}</Text>}
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
          </div>
        )}
        {askFailed && <Text size="sm" c="red" role="alert" data-testid="work-ask-error">{askFailed}</Text>}
      </Stack>
    </Paper>
  );
}
