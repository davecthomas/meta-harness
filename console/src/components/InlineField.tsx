import { type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { ActionIcon, Badge, Checkbox, Code, Group, Loader, NumberInput, Select, Stack, Switch, TagsInput, Text, TextInput, Textarea } from "@mantine/core";
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
  | { kind: "switch"; value: boolean; onCommit: Commit<boolean>; switchLabel?: ReactNode }
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

  function start() {
    if (props.readOnly || editingRef.current) return;
    editingRef.current = true;
    setErrors([]);
    setSearch("");
    setDraft(props.kind === "number" ? (props.value ?? "") : props.kind === "tags" ? [...props.value] : (props.value ?? (props.kind === "select" ? null : "")));
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
    if (same(local.value, props.value)) {
      if (!opts.direct) close();
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
      {props.changed && <Badge size="xs" color="yellow" variant="light" tt="none">changed</Badge>}
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
            aria-label={props.switchLabel ? undefined : props.label}
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
          description="Ctrl or Cmd+Enter to save, Escape to cancel"
          onChange={(e) => setDraft(e.currentTarget.value)}
          onKeyDown={(e) => keys(e, () => void commit(draft), true)}
          onBlur={() => void commit(draft)}
          rightSection={section}
          styles={{ input: { fontFamily: ff } }}
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
    } else {
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
            style={{ opacity: hover ? 1 : 0, transition: "opacity 120ms" }}
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
