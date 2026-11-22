import { useEffect, useState } from "react";
import { Button, Group, Loader, Paper, SegmentedControl, Stack, Text, Title, UnstyledButton } from "@mantine/core";
import { IconCheck, IconChevronDown, IconChevronRight, IconCopy } from "@tabler/icons-react";
import { call } from "../../client";
import { when } from "../../components/ui";

// A narrative as the planner serves it (planner/narratives.py): stored, and written again only when what it
// is about has changed.
export interface NarrativeRead {
  persona: string;
  label: string;
  text: string;
  written_at: string | null;
  // Older than what it is about: it could not be written again just now, and `why` says so.
  stale: boolean;
  why: string;
  goals?: number;
}
interface Persona { id: string; label: string }

const CHOSEN = "mh:narrative-persona";
function remembered(): string {
  try {
    return localStorage.getItem(CHOSEN) ?? "";
  } catch {
    return "";
  }
}

// A narrative summary for one kind of reader, for passing on: of a goal, or across goals on the Overview. It
// is folded until asked for, so opening a page writes nothing. The reader is picked from the ones the planning
// configuration lists, and the text is copied with one button.
export function Narrative({ title, path, watch }: {
  title: string;
  // Where the narrative is read: /v1/goals/<id>/narrative, or /v1/overview/narrative.
  path: string;
  // Changes when what the narrative is about does, so an open one is read again.
  watch?: string;
}) {
  const [opened, setOpened] = useState(false);
  const [personas, setPersonas] = useState<Persona[] | null>(null);
  const [persona, setPersona] = useState("");
  const [read, setRead] = useState<NarrativeRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState<"yes" | "no" | null>(null);

  useEffect(() => {
    if (!opened || personas) return;
    call<{ personas: Persona[] }>("/v1/overview/personas").then(
      (found) => {
        setPersonas(found.personas);
        const kept = remembered();
        setPersona(found.personas.some((p) => p.id === kept) ? kept : found.personas[0]?.id ?? "");
      },
      (err) => setError(`The readers could not be listed: ${err instanceof Error ? err.message : String(err)}. Close this and open it again.`),
    );
  }, [opened, personas]);

  useEffect(() => {
    if (!opened || !persona) return;
    let current = true;
    setLoading(true);
    setError(null);
    setCopied(null);
    call<NarrativeRead>(`${path}?persona=${encodeURIComponent(persona)}`).then(
      (found) => {
        if (!current) return;
        setRead(found);
        setLoading(false);
      },
      (err) => {
        if (!current) return;
        setRead(null);
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      },
    );
    return () => { current = false; };
  }, [opened, persona, path, watch]);

  function choose(next: string) {
    setPersona(next);
    try {
      localStorage.setItem(CHOSEN, next);
    } catch {
      // The choice is a convenience: without storage it lasts for this page.
    }
  }

  async function copy() {
    if (!read?.text) return;
    try {
      if (!navigator.clipboard) throw new Error("no clipboard");
      await navigator.clipboard.writeText(read.text);
      setCopied("yes");
    } catch {
      // A browser may refuse the clipboard: the text is on the page to select by hand.
      setCopied("no");
    }
  }

  return (
    <Paper withBorder p="sm" mb="md" data-testid="narrative">
      <Group justify="space-between" wrap="nowrap">
        <UnstyledButton onClick={() => setOpened((now) => !now)} aria-expanded={opened} aria-label={title}>
          <Group gap={6} wrap="nowrap">
            {opened ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
            <Title order={5}>{title}</Title>
            {!opened && <Text size="sm" c="dimmed">A summary to pass on, written for its reader.</Text>}
          </Group>
        </UnstyledButton>
        {opened && personas && personas.length > 1 && (
          <SegmentedControl
            size="xs"
            aria-label="Written for"
            value={persona}
            onChange={choose}
            data={personas.map((p) => ({ value: p.id, label: p.label }))}
          />
        )}
      </Group>
      {opened && (
        <Stack gap="xs" mt="sm">
          {personas && personas.length === 0 && (
            <Text size="sm">No reader is set up. An admin adds them under Configuration, Planning (narratives.personas).</Text>
          )}
          {loading && <Group gap="xs"><Loader size="xs" /><Text size="sm" c="dimmed">Reading the narrative. One that is out of date is written again first.</Text></Group>}
          {error && <Text size="sm" c="red" role="alert">{error}</Text>}
          {!loading && read && read.text && (
            <>
              <Text size="sm" style={{ whiteSpace: "pre-wrap" }} data-testid="narrative-text">{read.text}</Text>
              <Group gap="sm">
                <Button size="compact-sm" variant="default" leftSection={copied === "yes" ? <IconCheck size={14} /> : <IconCopy size={14} />} onClick={() => void copy()}>
                  {copied === "yes" ? "Copied" : "Copy"}
                </Button>
                <Text size="xs" c="dimmed">
                  {read.written_at ? `Written ${when(read.written_at)}.` : ""}
                  {read.stale ? ` It is older than what it describes: ${read.why}.` : " It is written again only when something changes."}
                </Text>
              </Group>
              {copied === "no" && <Text size="xs" c="orange">This browser would not copy it. Select the text above and copy it by hand.</Text>}
            </>
          )}
          {!loading && read && !read.text && <Text size="sm" c="dimmed">No goal is running, so there is nothing to summarize.</Text>}
        </Stack>
      )}
    </Paper>
  );
}
