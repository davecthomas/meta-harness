import { Autocomplete, Text } from "@mantine/core";
import { InlineField } from "./InlineField";

// The list's own spelling of a name, whatever capitals it was typed in; undefined when the list has none.
function onList(known: string[] | null, name: string): string | undefined {
  return (known ?? []).find((k) => k.toLowerCase() === name.trim().toLowerCase());
}

export type PickedNameProps = {
  // What the operator sees and the field's accessible name.
  label: string;
  // The kind of name, such as "status", and whose list it is, such as the project.
  what: string;
  where: string;
  value: string;
  // Shown when there is no value.
  empty: string;
  // The service's list: null while it loads.
  known: string[] | null;
  // Why the list cannot be read, else null.
  unreadable: string | null;
  testId: string;
  changed?: boolean;
  readOnly?: boolean;
  onCommit: (name: string) => void;
};

// A name that must match something a service can list, such as a status or an issue type: picked from that
// list, so it is not mistyped. A name the list lacks is refused, and one already saved is marked. When the
// list cannot be read (unreadable: why), or is empty, the name can still be typed.
export function PickedName(props: PickedNameProps) {
  const { known } = props;
  const listed = !!known?.length;
  return (
    <div>
      <InlineField
        kind="custom"
        label={props.label}
        value={props.value}
        display={props.value ? <Text size="sm">{props.value}</Text> : <Text size="sm" c="dimmed">{props.empty}</Text>}
        empty={<Text size="sm" c="dimmed">{props.empty}</Text>}
        changed={props.changed}
        readOnly={props.readOnly}
        check={(draft: string) =>
          listed && draft.trim() && !onList(known, draft) ? `${props.where} has no ${props.what} named ${draft.trim()}: pick one from the list, or leave it empty for the usual` : null
        }
        input={(draft) => (
          <Autocomplete
            aria-label={props.label}
            size="xs"
            w={220}
            placeholder={known === null && !props.unreadable ? "Loading…" : listed ? `Pick ${/^[aeiou]/i.test(props.what) ? "an" : "a"} ${props.what}` : "Type its name"}
            value={draft.value}
            onChange={draft.onChange}
            data={known ?? []}
            error={draft.invalid}
            maxDropdownHeight={260}
          />
        )}
        onCommit={(v: string) => props.onCommit(onList(known, v) ?? v.trim())}
      />
      {listed && props.value && !onList(known, props.value) ? (
        <Text size="xs" c="orange" data-testid={props.testId}>{props.where} has no {props.what} named {props.value}</Text>
      ) : null}
    </div>
  );
}

// Says why a list could not be read, and that each name must then be typed exactly.
export function Unreadable({ what, error }: { what: string; error: string | null }) {
  return error ? <Text size="xs" c="orange">The {what} could not be listed, so type each exactly: {error}</Text> : null;
}
