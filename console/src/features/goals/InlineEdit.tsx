import type { ReactNode } from "react";
import { Text } from "@mantine/core";
import { ApiError } from "../../api";
import { InlineField, type DraftProps } from "../../components/InlineField";

export type { DraftProps };

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

// A commit for InlineField from a save that throws when refused: the refusal becomes the reason under the input.
function saved<T>(save: (value: T) => Promise<unknown>) {
  return async (value: T): Promise<string | undefined> => {
    try {
      await save(value);
      return undefined;
    } catch (err) {
      return refusal(err);
    }
  };
}

// A goal's field that saves to the API as soon as it is committed. It is the console's one click-to-edit
// field (components/InlineField) with the goal's own input and a save that may be refused; how it opens,
// commits, cancels and shows a refusal is that component's.
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
  return (
    <InlineField
      kind="custom"
      label={props.label}
      value={props.value}
      display={props.display}
      readOnly={props.readOnly}
      multiline={props.multiline}
      heading={props.heading}
      same={props.same ?? Object.is}
      check={props.check}
      input={props.input}
      onCommit={saved(props.save)}
    />
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
  const text = props.checked ? props.onText : props.offText;
  if (props.readOnly) return <Text size="sm">{text}</Text>;
  return <InlineField kind="switch" label={props.label} nameByLabel switchLabel={text} value={props.checked} onCommit={saved(props.save)} />;
}
