import { AiTyping } from "../../components/AiWriting";
import { useEffect, useRef, useState } from "react";
import { Anchor, Text } from "@mantine/core";
import { api } from "../../api";
import type { GoalSpec } from "./SpecField";

const message = (err: unknown) => (err instanceof Error ? err.message : String(err));

// What a specification is called on the form: a file's name, or an issue's ref.
export function specName(spec: GoalSpec): string {
  return "ref" in spec ? spec.ref : spec.name;
}

type Made = { title: string; statement: string };

// The goal's title and description, written from its specification as soon as one is chosen. An operator who
// picked a spec has said what the goal is, so the form fills both in and leaves them to be edited. A field
// the operator already wrote is theirs: it is kept, and the form offers the spec's in its place. Removing
// the spec takes back what it filled in, unless that was edited since.
export function useSpecDraft({ spec, repos, tracker, trackerSettings, title, statement, setTitle, setStatement }: {
  spec: GoalSpec | null;
  repos: string[];
  tracker: string;
  trackerSettings: Record<string, string>;
  title: string;
  statement: string;
  setTitle: (next: string) => void;
  setStatement: (next: string) => void;
}) {
  const [writing, setWriting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // What the model wrote for the spec now chosen, and which of it is in the form.
  const [made, setMade] = useState<Made | null>(null);
  const filled = useRef<Partial<Made>>({});
  // The form as it is when an answer arrives, which is later than when it was asked for.
  const now = useRef({ title, statement, repos, tracker, trackerSettings });
  now.current = { title, statement, repos, tracker, trackerSettings };
  const [again, setAgain] = useState(0);
  const key = spec ? ("ref" in spec ? `ref:${spec.ref}` : `text:${spec.name}:${spec.text.length}`) : "";

  useEffect(() => {
    // Another spec, or none: what the last one filled in goes, where the operator has not changed it.
    const was = filled.current;
    if (was.title !== undefined && now.current.title === was.title) setTitle("");
    if (was.statement !== undefined && now.current.statement === was.statement) setStatement("");
    filled.current = {};
    setMade(null);
    setError(null);
    if (!spec) {
      setWriting(false);
      return;
    }
    let stale = false;
    setWriting(true);
    const form = now.current;
    api
      .draftFromSpec({
        spec,
        repos: form.repos,
        ...(form.tracker ? { tracker: form.tracker } : {}),
        ...(Object.keys(form.trackerSettings).length ? { tracker_settings: form.trackerSettings } : {}),
      })
      .then(
        (found) => {
          if (stale) return;
          const put: Partial<Made> = {};
          if (!now.current.title.trim()) {
            setTitle(found.title);
            put.title = found.title;
          }
          if (!now.current.statement.trim()) {
            setStatement(found.statement);
            put.statement = found.statement;
          }
          filled.current = put;
          setMade({ title: found.title, statement: found.statement });
        },
        (err) => !stale && setError(message(err)),
      )
      .finally(() => !stale && setWriting(false));
    return () => {
      stale = true;
    };
    // The spec alone decides: a change to the form's other fields asks for nothing.
  }, [key, again]);

  function useSpecs() {
    if (!made) return;
    setTitle(made.title);
    setStatement(made.statement);
    filled.current = { ...made };
  }

  if (!spec) return { writing, element: null };
  const name = specName(spec);
  const kept = made && (statement !== made.statement || title !== made.title) && filled.current.statement === undefined;
  // While it writes, the description field shows it inside (components/AiWriting.tsx): this line names the source.
  const element = writing ? (
    <div data-testid="spec-draft-writing">
      <AiTyping>AI is reading {name}</AiTyping>
    </div>
  ) : error ? (
    <Text size="sm" c="orange" role="alert" data-testid="spec-draft-failed">
      The title and description were not written from {name}: {error}{" "}
      <Anchor component="button" type="button" size="sm" onClick={() => setAgain((n) => n + 1)}>Try again</Anchor>
    </Text>
  ) : kept ? (
    <Text size="sm" c="dimmed" data-testid="spec-draft-kept">
      You wrote the description, so it is kept.{" "}
      <Anchor component="button" type="button" size="sm" onClick={useSpecs}>Use the one written from {name}</Anchor>
    </Text>
  ) : made ? (
    <Text size="sm" c="dimmed" data-testid="spec-draft-filled">Written from {name}. Edit them as you like: the planner reads the spec itself.</Text>
  ) : null;
  return { writing, element };
}
