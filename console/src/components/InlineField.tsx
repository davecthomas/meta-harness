import { type FocusEvent, type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { ActionIcon, Badge, Box, Checkbox, Code, Group, Loader, NumberInput, Select, Stack, Switch, TagsInput, Text, TextInput, Textarea } from "@mantine/core";
import { IconPencil } from "@tabler/icons-react";

// What a commit answers: nothing when the value is taken, or the reasons it is refused. A thrown error refuses too.
export type CommitResult = void | null | undefined | string | string[];
type Commit<T> = (value: T) => CommitResult | Promise<CommitResult>;
export type FieldOption = string | { value: string; label: string };

interface Base {
  // Names the value: the input's label, and the pencil's ("Edit <label>").
  label: string;
  // How the value reads when it is not being edited; each kind has a default.
  display?: ReactNode;
  // What shows when the value is empty.
  empty?: ReactNode;
  // Reasons about this value from elsewhere (a refused save), shown under it.
  reasons?: string[];
  // Marks the value as changed and not yet saved.
  changed?: boolean;
  // Shows the value without a way to change it.
  readOnly?: boolean;
  // Shows "Saved" beside the value for a moment after a commit is taken.
  showSaved?: boolean;
  monospace?: boolean;
  placeholder?: string;
}

// What a custom kind's own input is given: the draft, how to change it, and whether it was last refused.
export interface DraftProps<T> {
  value: T;
  onChange: (value: T) => void;
  invalid: boolean;
}

type Kinds =
  | { kind: "text"; value: string | null; onCommit: Commit<string> }
  | { kind: "textarea"; value: string | null; onCommit: Commit<string> }
  | {
      kind: "number";
      value: number | null;
      onCommit: Commit<number | null>;
      min?: number;
      max?: number;
      step?: number;
      decimalScale?: number;
      // Whether the value may be emptied; an empty value commits null.
      allowEmpty?: boolean;
      prefix?: string;
      suffix?: string;
    }
  | { kind: "select"; value: string | null; data: FieldOption[]; onCommit: Commit<string | null>; clearable?: boolean; searchable?: boolean }
  // nameByLabel: the switch's accessible name is `label` even with a switchLabel, whose words change with its value.
  | { kind: "switch"; value: boolean; onCommit: Commit<boolean>; switchLabel?: ReactNode; nameByLabel?: boolean }
  // A value with an input of its own (several inputs, a picker), drawn by `input` from the draft. Leaving the
  // inputs or Enter commits; multiline: Ctrl/Cmd+Enter does. check: why the draft cannot be committed, asked
  // before it is. heading: the value sits in a page heading, so it takes no button of its own there.
  | {
      kind: "custom";
      value: any;
      onCommit: Commit<any>;
      input: (draft: DraftProps<any>) => ReactNode;
      same?: (a: any, b: any) => boolean;
      check?: (draft: any) => string | null;
      multiline?: boolean;
      heading?: boolean;
    }
  | { kind: "checkboxes"; value: string[]; data: FieldOption[]; onCommit: Commit<string[]> }
  | { kind: "tags"; value: string[]; onCommit: Commit<string[]>; data?: string[] };

export type InlineFieldProps = Base & Kinds;

const SAVED_MS = 2000;

function optionValue(o: FieldOption): string {
  return typeof o === "string" ? o : o.value;
}

function optionLabel(data: FieldOption[], value: string | null): string | null {
  if (value === null || value === undefined) return null;
  const found = data.find((o) => optionValue(o) === value);
  return found === undefined ? value : typeof found === "string" ? found : found.label;
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

function refusal(result: CommitResult): string[] | null {
  if (Array.isArray(result)) return result.length ? result : null;
  return result ? [result] : null;
}

// The one way a value edits where it is shown. Hovering shows a pencil; clicking the value or the pencil
// turns it into its input. Enter or leaving the field commits, Escape cancels, and a multi-line field commits
// with Ctrl/Cmd+Enter. A switch or checkboxes commit on change. A refused value keeps the input open with
// the reason under it.
export function InlineField(props: InlineFieldProps) {
  const [editing, setEditing] = useState(false);
  const editingRef = useRef(false);
  const busyRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<any>(null);
  const [search, setSearch] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [hover, setHover] = useState(false);
  const [saved, setSaved] = useState(false);
  // A switch's or the checkboxes' value while its commit is in flight.
  const [pending, setPending] = useState<any>(undefined);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  // A custom kind's inputs are its own, so the field finds the first to focus when it opens.
  const box = useRef<HTMLDivElement>(null);
  const custom = props.kind === "custom";
  useEffect(() => {
    if (editing && custom) box.current?.querySelector<HTMLElement>("input, textarea")?.focus();
  }, [editing, custom]);

  function start() {
    if (props.readOnly || editingRef.current) return;
    editingRef.current = true;
    setErrors([]);
    setSearch("");
    setDraft(props.kind === "custom" ? props.value : props.kind === "number" ? (props.value ?? "") : props.kind === "tags" ? [...props.value] : (props.value ?? (props.kind === "select" ? null : "")));
    setEditing(true);
  }

  function close() {
    editingRef.current = false;
    setEditing(false);
    setErrors([]);
  }

  function flash() {
    if (!props.showSaved) return;
    setSaved(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setSaved(false), SAVED_MS);
  }

  // The value as the kind commits it, or why it cannot be.
  function normalize(next: any): { value: unknown } | { error: string } {
    if (props.kind === "number") {
      if (next === "" || next === null || next === undefined) {
        return props.allowEmpty ? { value: null } : { error: "A number is required" };
      }
      const n = typeof next === "number" ? next : Number(next);
      if (!Number.isFinite(n)) return { error: "Not a number" };
      if (props.min !== undefined && n < props.min) return { error: `At least ${props.min}` };
      if (props.max !== undefined && n > props.max) return { error: `At most ${props.max}` };
      return { value: n };
    }
    if (props.kind === "text") return { value: String(next ?? "").trim() };
    if (props.kind === "tags") return { value: Array.from(new Set((next as string[]).map((t) => t.trim()).filter(Boolean))) };
    return { value: next };
  }

  async function commit(next: any, opts: { direct?: boolean } = {}) {
    if (busyRef.current) return;
    if (!opts.direct && !editingRef.current) return;
    const local = normalize(next);
    if ("error" in local) {
      setErrors([local.error]);
      return;
    }
    if (props.kind === "custom" && props.same ? props.same(local.value, props.value) : same(local.value, props.value)) {
      if (!opts.direct) close();
      return;
    }
    const why = props.kind === "custom" ? (props.check?.(local.value) ?? null) : null;
    if (why) {
      setErrors([why]);
      return;
    }
    busyRef.current = true;
    setBusy(true);
    if (opts.direct) setPending(local.value);
    try {
      const refused = refusal(await (props.onCommit as Commit<unknown>)(local.value));
      if (refused) {
        setErrors(refused);
        return;
      }
      if (opts.direct) setErrors([]);
      else close();
      flash();
    } catch (err) {
      setErrors([err instanceof Error ? err.message : String(err)]);
    } finally {
      busyRef.current = false;
      setBusy(false);
      if (opts.direct) setPending(undefined);
    }
  }

  function cancel() {
    if (busyRef.current) return;
    close();
  }

  function keys(e: KeyboardEvent, onEnter: () => void, multiline = false) {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      cancel();
    } else if (e.key === "Enter" && (!multiline || e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      onEnter();
    }
  }

  const shownReasons = [...errors, ...(props.reasons ?? []).filter((r) => !errors.includes(r))];
  const marks = (
    <>
      {props.changed && <Badge size="xs" color="yellow" variant="light" tt="none">not saved</Badge>}
      {busy && <Loader size="xs" />}
      {saved && <Text size="xs" c="teal" fw={500}>Saved</Text>}
    </>
  );
  const reasonsBlock = shownReasons.length > 0 && (
    <Stack gap={2}>
      {shownReasons.map((r) => <Text key={r} size="xs" c="red">{r}</Text>)}
    </Stack>
  );

  // A switch and checkboxes are their own input: they commit on change, with no edit mode.
  if (props.kind === "switch") {
    const checked = pending === undefined ? props.value : Boolean(pending);
    return (
      <Stack gap={2}>
        <Group gap={6} wrap="nowrap">
          <Switch
            aria-label={props.switchLabel && !props.nameByLabel ? undefined : props.label}
            label={props.switchLabel}
            checked={checked}
            disabled={props.readOnly || busy}
            onChange={(e) => void commit(e.currentTarget.checked, { direct: true })}
          />
          {marks}
        </Group>
        {reasonsBlock}
      </Stack>
    );
  }
  if (props.kind === "checkboxes") {
    const value: string[] = pending === undefined ? props.value : pending;
    return (
      <Stack gap={2}>
        <Group gap={6} wrap="nowrap" align="flex-start">
          <Checkbox.Group aria-label={props.label} value={value} onChange={(v) => void commit(v, { direct: true })}>
            <Stack gap="xs">
              {props.data.map((o) => (
                <Checkbox key={optionValue(o)} value={optionValue(o)} label={optionLabel(props.data, optionValue(o))} disabled={props.readOnly || busy} />
              ))}
            </Stack>
          </Checkbox.Group>
          {marks}
        </Group>
        {props.data.length === 0 && <Text size="sm" c="dimmed">{props.empty ?? "None to choose from"}</Text>}
        {reasonsBlock}
      </Stack>
    );
  }

  if (editing && props.kind === "custom") {
    const multiline = props.multiline;
    return (
      // Focusable itself, so a click on a label or the space between its inputs stays within the field.
      <Box
        ref={box}
        tabIndex={-1}
        style={{ outline: "none" }}
        onKeyDown={(e: KeyboardEvent) => {
          if (e.key === "Escape") {
            e.preventDefault();
            cancel();
          } else if (e.key === "Enter" && !e.defaultPrevented && !e.nativeEvent.isComposing) {
            // A combobox that picks its highlighted option on Enter prevents the default, and that pick is not a commit.
            if (multiline && !(e.ctrlKey || e.metaKey)) return;
            e.preventDefault();
            void commit(draft);
          }
        }}
        onBlur={(e: FocusEvent<HTMLDivElement>) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) void commit(draft);
        }}
      >
        <Stack gap={4}>
          {props.input({ value: draft, onChange: setDraft, invalid: errors.length > 0 })}
          {busy && (
            <Group gap={6}>
              <Loader size={12} />
              <Text size="xs" c="dimmed">Saving…</Text>
            </Group>
          )}
          {shownReasons.map((r) => <Text key={r} size="xs" c="red" role="alert">{r}</Text>)}
          {!busy && !shownReasons.length && <SaveHint multiline={multiline} />}
        </Stack>
      </Box>
    );
  }

  // In a page heading the value names the heading: it opens on a click or Enter, with its pencil beside it and
  // no button role, which would take the heading's name.
  if (props.kind === "custom" && props.heading) {
    if (props.readOnly) return <>{props.display}</>;
    return (
      <Box
        component="span"
        tabIndex={0}
        title={`Edit ${props.label}`}
        onClick={start}
        onKeyDown={(e: KeyboardEvent) => {
          if (e.key === "Enter") {
            e.preventDefault();
            start();
          }
        }}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        onFocus={() => setHover(true)}
        onBlur={() => setHover(false)}
        style={{ cursor: "pointer", display: "inline", maxWidth: "100%" }}
      >
        {props.display}
        <IconPencil aria-hidden size={18} style={{ opacity: hover ? 0.7 : 0.35, marginLeft: 8, marginTop: 3, transition: "opacity 120ms" }} />
      </Box>
    );
  }

  if (editing) {
    const ff = props.monospace ? "var(--mantine-font-family-monospace)" : undefined;
    const section = busy ? <Loader size="xs" /> : undefined;
    let input: ReactNode;
    if (props.kind === "text") {
      input = (
        <TextInput
          aria-label={props.label}
          autoFocus
          size="xs"
          value={draft ?? ""}
          placeholder={props.placeholder}
          onChange={(e) => setDraft(e.currentTarget.value)}
          onKeyDown={(e) => keys(e, () => void commit(draft))}
          onBlur={() => void commit(draft)}
          rightSection={section}
          styles={{ input: { fontFamily: ff } }}
        />
      );
    } else if (props.kind === "textarea") {
      input = (
        <Textarea
          aria-label={props.label}
          autoFocus
          size="xs"
          autosize
          minRows={2}
          value={draft ?? ""}
          placeholder={props.placeholder}
          // The description is tied to the textarea (aria-describedby); help, so at the body's size, not xs.
          description={SAVE_HINT_MULTILINE}
          onChange={(e) => setDraft(e.currentTarget.value)}
          onKeyDown={(e) => keys(e, () => void commit(draft), true)}
          onBlur={() => void commit(draft)}
          rightSection={section}
          styles={{ input: { fontFamily: ff }, description: { fontSize: "var(--mantine-font-size-sm)" } }}
        />
      );
    } else if (props.kind === "number") {
      input = (
        <NumberInput
          aria-label={props.label}
          autoFocus
          size="xs"
          w={160}
          value={draft ?? ""}
          min={props.min}
          max={props.max}
          step={props.step}
          decimalScale={props.decimalScale}
          allowDecimal={props.decimalScale !== 0}
          prefix={props.prefix}
          suffix={props.suffix}
          placeholder={props.placeholder}
          clampBehavior="none"
          onChange={(v) => setDraft(v)}
          onKeyDown={(e) => keys(e, () => void commit(draft))}
          onBlur={() => void commit(draft)}
          rightSection={section}
        />
      );
    } else if (props.kind === "select") {
      input = (
        <Select
          aria-label={props.label}
          autoFocus
          size="xs"
          defaultDropdownOpened
          data={props.data}
          value={draft}
          clearable={props.clearable}
          searchable={props.searchable}
          placeholder={props.placeholder}
          onChange={(v) => {
            setDraft(v);
            void commit(v);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              cancel();
            }
          }}
          onBlur={() => void commit(draft)}
          rightSection={section}
          comboboxProps={{ withinPortal: true }}
        />
      );
    } else if (props.kind === "tags") {
      const add = search.trim();
      input = (
        <TagsInput
          aria-label={props.label}
          autoFocus
          size="xs"
          data={props.data}
          value={draft ?? []}
          searchValue={search}
          onSearchChange={setSearch}
          onChange={setDraft}
          placeholder={props.placeholder ?? "Type and press Enter to add; Enter again to save"}
          clearable
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              cancel();
            } else if (e.key === "Enter" && !add) {
              e.preventDefault();
              void commit(draft);
            }
          }}
          onBlur={() => void commit(add ? [...(draft ?? []), add] : draft)}
          rightSection={section}
          styles={{ pill: { fontFamily: ff } }}
        />
      );
    }
    return (
      <Stack gap={2}>
        {input}
        {reasonsBlock}
      </Stack>
    );
  }

  return (
    <Stack gap={2}>
      <Group
        gap={6}
        wrap="nowrap"
        align="center"
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        onFocus={() => setHover(true)}
        onBlur={() => setHover(false)}
      >
        <div
          onClick={start}
          style={{ cursor: props.readOnly ? undefined : "pointer", minWidth: 0 }}
          data-inline-field={props.label}
        >
          {props.display ?? <DefaultDisplay {...props} />}
        </div>
        {!props.readOnly && (
          <ActionIcon
            size="sm"
            variant="subtle"
            color="gray"
            aria-label={`Edit ${props.label}`}
            onClick={start}
            // Always there, dimmer until pointed at: a value that can be edited looks it, on a touch screen too.
            style={{ opacity: hover ? 1 : 0.45, transition: "opacity 120ms" }}
          >
            <IconPencil size={14} />
          </ActionIcon>
        )}
        {marks}
      </Group>
      {reasonsBlock}
    </Stack>
  );
}

// How an open field saves and cancels: help, so it reads at the body's size and never small and dimmed at once.
const SAVE_HINT_MULTILINE = "Ctrl+Enter or ⌘+Enter saves, Escape cancels.";

function SaveHint({ multiline }: { multiline?: boolean }) {
  return <Text size="sm" c="dimmed">{multiline ? SAVE_HINT_MULTILINE : "Enter saves, Escape cancels."}</Text>;
}

function Empty({ children }: { children?: ReactNode }) {
  return <Text size="sm" c="dimmed">{children ?? "None"}</Text>;
}

function DefaultDisplay(props: InlineFieldProps) {
  if (props.kind === "tags") {
    if (!props.value.length) return <Empty>{props.empty}</Empty>;
    return props.monospace ? (
      <Stack gap={2}>{props.value.map((t) => <Code key={t}>{t}</Code>)}</Stack>
    ) : (
      <Group gap={4}>{props.value.map((t) => <Badge key={t} size="sm" variant="light" tt="none">{t}</Badge>)}</Group>
    );
  }
  if (props.kind === "select") {
    const label = optionLabel(props.data, props.value);
    return label === null || label === "" ? <Empty>{props.empty}</Empty> : <Text size="sm">{label}</Text>;
  }
  if (props.kind === "number") {
    if (props.value === null || props.value === undefined) return <Empty>{props.empty}</Empty>;
    return <Text size="sm">{props.prefix ?? ""}{props.value}{props.suffix ?? ""}</Text>;
  }
  if (props.kind === "text" || props.kind === "textarea") {
    if (!props.value) return <Empty>{props.empty}</Empty>;
    return (
      <Text size="sm" ff={props.monospace ? "monospace" : undefined} style={props.kind === "textarea" ? { whiteSpace: "pre-wrap" } : undefined}>
        {props.value}
      </Text>
    );
  }
  return null;
}
