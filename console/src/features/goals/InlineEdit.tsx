import { type FocusEvent, type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { Box, Group, Loader, Stack, Switch, Text } from "@mantine/core";
import { IconPencil } from "@tabler/icons-react";
import { ApiError } from "../../api";

// Why a save was refused, in words: the API's reason, or each of a validation error's messages.
export function refusal(err: unknown): string {
  if (err instanceof ApiError && err.detail.startsWith("[")) {
    try {
      const items = JSON.parse(err.detail) as { msg?: string; loc?: unknown[] }[];
      return items.map((i) => `${i.loc?.length ? `${i.loc[i.loc.length - 1]}: ` : ""}${i.msg ?? ""}`).join("; ");
    } catch {
      return err.detail;
    }
  }
  return err instanceof Error ? err.message : String(err);
}

export interface DraftProps<T> {
  value: T;
  onChange: (value: T) => void;
  // Whether the last save or check was refused, for the input to show it.
  invalid: boolean;
}

// One field shown as its value that turns into its input where it is: on a click, on Enter when focused, or on
// its pencil icon, shown on hover. Enter or leaving the field saves it, Escape cancels, and a multi-line field
// saves with Ctrl/Cmd+Enter. A save that is the same as the value sends nothing. While it saves the field says
// so; a refused save keeps the input open with the reason under it. Read-only, it is only its value.
export function InlineEdit<T>(props: {
  // What the field is, for its accessible name ("Edit budget").
  label: string;
  value: T;
  display: ReactNode;
  readOnly?: boolean;
  multiline?: boolean;
  // In the page heading: the value names the heading, so the field takes no button role or name of its own.
  heading?: boolean;
  same?: (a: T, b: T) => boolean;
  // Why the draft cannot be saved, checked before it is sent; null when it can.
  check?: (draft: T) => string | null;
  save: (draft: T) => Promise<unknown>;
  input: (draft: DraftProps<T>) => ReactNode;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<T>(props.value);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [hover, setHover] = useState(false);
  // Refs, not state: a blur and a key press in the same moment must see one save, and none after a cancel.
  const open = useRef(false);
  const busy = useRef(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (editing) box.current?.querySelector<HTMLElement>("input, textarea")?.focus();
  }, [editing]);

  function begin() {
    if (props.readOnly) return;
    setDraft(props.value);
    setError(null);
    open.current = true;
    setEditing(true);
  }

  function close() {
    open.current = false;
    setEditing(false);
    setError(null);
    setHover(false);
  }

  async function commit() {
    if (!open.current || busy.current) return;
    if ((props.same ?? Object.is)(draft, props.value)) return close();
    const why = props.check?.(draft) ?? null;
    if (why) return setError(why);
    busy.current = true;
    setSaving(true);
    setError(null);
    try {
      await props.save(draft);
      close();
    } catch (err) {
      setError(refusal(err));
    } finally {
      busy.current = false;
      setSaving(false);
    }
  }

  function keys(e: KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      if (!busy.current) close();
    } else if (e.key === "Enter" && !e.defaultPrevented && !e.nativeEvent.isComposing) {
      // A combobox that picks its highlighted option on Enter prevents the default, and that pick is not a save.
      if (props.multiline && !(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      void commit();
    }
  }

  function left(e: FocusEvent<HTMLDivElement>) {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) void commit();
  }

  if (props.readOnly) return <>{props.display}</>;

  if (editing) {
    return (
      // Focusable itself, so a click on a label or the space between inputs stays within the field.
      <Box ref={box} tabIndex={-1} onKeyDown={keys} onBlur={left} style={{ outline: "none" }}>
        <Stack gap={4}>
          {props.input({ value: draft, onChange: setDraft, invalid: Boolean(error) })}
          {saving && (
            <Group gap={6}>
              <Loader size={12} />
              <Text size="xs" c="dimmed">Saving…</Text>
            </Group>
          )}
          {error && <Text size="xs" c="red" role="alert">{error}</Text>}
          {!saving && !error && (
            <Text size="xs" c="dimmed">
              {props.multiline ? "Ctrl+Enter or ⌘+Enter saves" : "Enter saves"}, Escape cancels.
            </Text>
          )}
        </Stack>
      </Box>
    );
  }

  return (
    <Box
      component="span"
      tabIndex={0}
      role={props.heading ? undefined : "button"}
      aria-label={props.heading ? undefined : `Edit ${props.label}`}
      title={`Edit ${props.label}`}
      onClick={begin}
      onKeyDown={(e: KeyboardEvent) => {
        if (e.key === "Enter" || (e.key === " " && !props.heading)) {
          e.preventDefault();
          begin();
        }
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onFocus={() => setHover(true)}
      onBlur={() => setHover(false)}
      style={{
        cursor: "pointer",
        display: props.heading ? "inline" : "flex",
        alignItems: "flex-start",
        gap: 6,
        maxWidth: "100%",
      }}
    >
      {props.display}
      <IconPencil
        aria-hidden
        size={props.heading ? 18 : 14}
        style={{ opacity: hover ? 0.6 : 0, flex: "none", marginLeft: props.heading ? 8 : 0, marginTop: 3, transition: "opacity 120ms" }}
      />
    </Box>
  );
}

// A switch that saves when flipped, says it is saving, and shows a refusal under it. Read-only, its value in words.
export function InlineSwitch(props: {
  label: string;
  checked: boolean;
  onText: string;
  offText: string;
  readOnly?: boolean;
  save: (checked: boolean) => Promise<unknown>;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const text = props.checked ? props.onText : props.offText;
  if (props.readOnly) return <Text size="sm">{text}</Text>;

  async function flip(checked: boolean) {
    setSaving(true);
    setError(null);
    try {
      await props.save(checked);
    } catch (err) {
      setError(refusal(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Stack gap={4}>
      <Group gap="sm">
        <Switch
          aria-label={props.label}
          label={text}
          checked={props.checked}
          disabled={saving}
          onChange={(e) => void flip(e.currentTarget.checked)}
        />
        {saving && (
          <Group gap={6}>
            <Loader size={12} />
            <Text size="xs" c="dimmed">Saving…</Text>
          </Group>
        )}
      </Group>
      {error && <Text size="xs" c="red" role="alert">{error}</Text>}
    </Stack>
  );
}
