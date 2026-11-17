import { useEffect, useState } from "react";
import { Autocomplete, NativeSelect, Stack, Text, TextInput } from "@mantine/core";
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
      {props.trackers.length > 1 && (
        <NativeSelect
          label="Issue tracker"
          description="Where the goal's issues are. A goal uses one tracker for all its issues, and keeps it."
          value={props.tracker}
          onChange={(e) => props.onTracker(e.currentTarget.value)}
          data={props.trackers.map((t) => ({ value: t.scheme, label: t.signed_in ? t.title : `${t.title} (not signed in)` }))}
        />
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

// A setting chosen from what the tracker lists, such as the Jira site's projects by name. The value is the
// choice's key (a project's key); when the list cannot be read the key can still be typed, and the field says why.
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
  const typed = props.value.trim();
  const known = (choices ?? []).some((c) => c.value === typed);
  return (
    <Stack gap={4}>
      <Autocomplete
        label={props.label}
        description={props.help}
        required={props.required}
        placeholder={choices === null && !error ? "Loading…" : "Choose one, or type its key"}
        value={props.value}
        // Picking a choice gives its key; typing gives what was typed, in capitals as keys are.
        onChange={(value) => props.onChange((choices ?? []).find((c) => c.label === value)?.value ?? value.toUpperCase())}
        data={(choices ?? []).map((c) => c.label)}
        maxDropdownHeight={280}
      />
      {error && <Text size="xs" c="orange">The list could not be read, so type the key exactly: {error}</Text>}
      {!error && choices !== null && typed && !known && <Text size="xs" c="orange">No {props.label.toLowerCase()} has the key {typed}.</Text>}
      {known && <Text size="xs" c="dimmed">{choices!.find((c) => c.value === typed)!.label}</Text>}
    </Stack>
  );
}
