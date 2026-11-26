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
  // A new one is being written in the background: the page reads again until it is there.
  writing?: boolean;
  goals?: number;
}
interface Persona { id: string; label: string }

const CHOSEN = "mh:narrative-persona";
// How often the page asks again while a narrative is being written.
export const AGAIN_MS = 2500;
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
    let again: ReturnType<typeof setTimeout> | undefined;
    setLoading(true);
    setError(null);
    setCopied(null);
    setRead(null);
    // A narrative is written in the background, which can take a minute: the read answers at once, and the
    // page asks again until the new one is there.
    const look = () =>
      call<NarrativeRead>(`${path}?persona=${encodeURIComponent(persona)}`).then(
        (found) => {
          if (!current) return;
          setRead(found);
          setLoading(false);
          if (found.writing) again = setTimeout(look, AGAIN_MS);
        },
        (err) => {
          if (!current) return;
          setRead(null);
          setError(err instanceof Error ? err.message : String(err));
          setLoading(false);
        },
      );
    void look();
    return () => {
      current = false;
      clearTimeout(again);
    };
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
          {loading && <Group gap="xs"><Loader size="xs" /><Text size="sm" c="dimmed">Reading the narrative.</Text></Group>}
          {!loading && read?.writing && (
            <Group gap="xs" data-testid="narrative-writing" role="status">
              <Loader size="xs" />
              <Text size="sm" c="dimmed">
                {read.text ? "The goal has changed, so a new one is being written. This is the one before it." : "Writing it now. That takes up to a minute, and it appears here when it is done."}
              </Text>
            </Group>
          )}
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
                  {read.writing ? "" : read.stale ? ` It is older than what it describes: ${read.why}.` : " It is written again only when something changes."}
                </Text>
              </Group>
              {copied === "no" && <Text size="xs" c="orange">This browser would not copy it. Select the text above and copy it by hand.</Text>}
            </>
          )}
          {!loading && read && !read.text && !read.writing && <Text size="sm" c="dimmed">No goal is running, so there is nothing to summarize.</Text>}
        </Stack>
      )}
    </Paper>
  );
}
