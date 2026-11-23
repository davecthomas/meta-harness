// Created by Octofusion in run t-33d72d7e8075:run-1 on harness:pi-lean/claude:claude-sonnet-5-5.

import { useEffect, useRef, useState } from "react";
import { Button, Group, Paper, Stack, Text, TextInput } from "@mantine/core";
import { api, ApiError } from "../../api";
import { when } from "../../components/ui";
import type { ReportItem, WorkAnswer, WorkReportAnswer } from "./api";

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

function Items({ title, items }: { title: string; items: ReportItem[] }) {
  if (!items.length) return null;
  return (
    <Stack gap={2}>
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{title}</Text>
      {items.map((item, i) => (
        <Text key={i} size="sm">{item.goal ? item.goal + ": " : ""}{item.what ?? item.why}{item.what && item.why ? " " + item.why : ""}</Text>
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
            <Items title="Needs you" items={report.needs_you} />
            <Items title="Moving" items={report.moving} />
            <Items title="Not moving" items={report.not_moving} />
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
