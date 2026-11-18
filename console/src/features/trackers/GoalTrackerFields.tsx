import { useEffect, useState } from "react";
import { NativeSelect, Select, Stack, Switch, Text, TextInput } from "@mantine/core";
import { api, type Tracker } from "../../api";

// The New goal form's part about where the goal's issues are: which tracker, when more than one is set up, and
// what that tracker asks of a goal, drawn from what its adapter says it needs (Jira: the project). A goal uses
// one tracker for all its issues, and keeps it and these answers.
export function GoalTrackerFields(props: {
  // The trackers that are set up.
  trackers: Tracker[];
  tracker: string;
  onTracker: (scheme: string) => void;
  settings: Record<string, string>;
  onSettings: (settings: Record<string, string>) => void;
}) {
  const chosen = props.trackers.find((t) => t.scheme === props.tracker);
  return (
    <Stack gap="lg">
      {props.trackers.length > 1 ? (
        <NativeSelect
          label="Issue tracker"
          description="Where the goal's issues are. A goal uses one tracker for all its issues, and keeps it."
          value={props.tracker}
          onChange={(e) => props.onTracker(e.currentTarget.value)}
          data={props.trackers.map((t) => ({ value: t.scheme, label: t.signed_in ? t.title : `${t.title} (not signed in)` }))}
        />
      ) : (
        // One tracker set up: still said, so the form never leaves where the issues are unsaid.
        <div data-testid="one-tracker">
          <Text size="sm" fw={500}>Issue tracker</Text>
          <Text size="sm">{chosen?.title ?? "None is set up"}</Text>
          <Text size="xs" c="dimmed">
            {chosen ? "The only one set up. An admin sets up others under Configuration, Issue trackers." : "A goal reads its issues from an issue tracker: an admin sets one up under Configuration, Issue trackers."}
          </Text>
        </div>
      )}
      {chosen && !chosen.signed_in && (
        <Text size="sm" c="orange" data-testid="tracker-signed-out">
          {chosen.title} has no sign-in yet, so a goal that uses it reads no issues until an admin adds one (Configuration, Issue trackers says which is missing).
        </Text>
      )}
      {(chosen?.goal_settings ?? []).map((setting) =>
        setting.kind === "choice" ? (
          <ChoiceField
            key={`${chosen!.scheme}:${setting.name}`}
            scheme={chosen!.scheme}
            name={setting.name}
            label={setting.label}
            help={setting.help}
            required={setting.required}
            value={props.settings[setting.name] ?? ""}
            onChange={(value) => props.onSettings({ ...props.settings, [setting.name]: value })}
          />
        ) : setting.kind === "switch" ? (
          // On is "yes"; off leaves the setting out, as a goal that never had it.
          <Switch
            key={`${chosen!.scheme}:${setting.name}`}
            label={setting.label}
            description={setting.help}
            checked={props.settings[setting.name] === "yes"}
            onChange={(e) => {
              const { [setting.name]: _off, ...rest } = props.settings;
              props.onSettings(e.currentTarget.checked ? { ...rest, [setting.name]: "yes" } : rest);
            }}
          />
        ) : (
          <TextInput
            key={`${chosen!.scheme}:${setting.name}`}
            label={setting.label}
            description={setting.help}
            required={setting.required}
            value={props.settings[setting.name] ?? ""}
            onChange={(e) => props.onSettings({ ...props.settings, [setting.name]: e.currentTarget.value })}
          />
        ),
      )}
    </Stack>
  );
}

// A setting chosen from what the tracker lists, such as the Jira projects the meta-harness works in. It is a
// pick, never free text: nothing half-typed reaches the goal, or the calls that read its issues. Only when the
// list cannot be read is the key typed, and then it counts once the field is left.
function ChoiceField(props: { scheme: string; name: string; label: string; help: string; required: boolean; value: string; onChange: (value: string) => void }) {
  const [choices, setChoices] = useState<{ value: string; label: string }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let stale = false;
    setChoices(null);
    setError(null);
    api.trackerChoices(props.scheme, props.name).then(
      (found) => !stale && setChoices(found),
      (err) => !stale && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      stale = true;
    };
  }, [props.scheme, props.name]);
  const chosen = props.value.trim();
  if (error) {
    return (
      <Stack gap={4}>
        <TypedKey label={props.label} help={props.help} required={props.required} value={props.value} onChange={props.onChange} />
        <Text size="xs" c="orange">The list could not be read, so type the key exactly: {error}</Text>
      </Stack>
    );
  }
  const known = (choices ?? []).some((c) => c.value === chosen);
  return (
    <Stack gap={4}>
      <Select
        label={props.label}
        description={props.help}
        required={props.required}
        searchable
        allowDeselect={false}
        disabled={choices === null}
        placeholder={choices === null ? "Loading…" : "Choose one"}
        nothingFoundMessage="None matches"
        value={known ? chosen : null}
        onChange={(value) => value && props.onChange(value)}
        data={choices ?? []}
        maxDropdownHeight={280}
      />
      {choices !== null && chosen && !known && <Text size="xs" c="orange">{chosen} is not one of these: choose one that is.</Text>}
    </Stack>
  );
}

// A key typed by hand, in capitals as keys are, which counts once the field is left.
function TypedKey(props: { label: string; help: string; required: boolean; value: string; onChange: (value: string) => void }) {
  const [draft, setDraft] = useState(props.value);
  return (
    <TextInput
      label={props.label}
      description={props.help}
      required={props.required}
      value={draft}
      onChange={(e) => setDraft(e.currentTarget.value.toUpperCase())}
      onBlur={() => draft.trim() !== props.value && props.onChange(draft.trim())}
    />
  );
}
