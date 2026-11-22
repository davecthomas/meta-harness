import { useEffect, useState } from "react";
import { NativeSelect, Select, Stack, Switch, Text, TextInput } from "@mantine/core";
import { api, type Tracker } from "../../api";
import type { TrackerSetting } from "./api";

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
  // The goal's repos, for a choice whose list depends on them (GitHub's assignable users).
  repos?: string[];
}) {
  const chosen = props.trackers.find((t) => t.scheme === props.tracker);
  // A choice left empty is left out, as a goal that never had it.
  function choose(name: string, value: string) {
    const { [name]: _dropped, ...rest } = props.settings;
    props.onSettings(value ? { ...rest, [name]: value } : rest);
  }
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
          <Text size="sm" c="dimmed">
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
            {...scopeOf(setting, chosen!.goal_settings, props.settings, props.repos)}
            value={props.settings[setting.name] ?? ""}
            onChange={(value) => choose(setting.name, value)}
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

// What a choice's list is asked with (the setting's `scope`): the other goal settings it names, such as Jira's
// project, and `repos`, the goal's repos joined by commas. While one is still empty, `waiting` says which, and
// the list is not asked for.
function scopeOf(setting: TrackerSetting, all: TrackerSetting[], settings: Record<string, string>, repos?: string[]): { scope: Record<string, string>; waiting: string } {
  const scope: Record<string, string> = {};
  const missing: string[] = [];
  for (const name of setting.scope ?? []) {
    const value = name === "repos" ? (repos ?? []).join(",") : (settings[name] ?? "").trim();
    if (value) scope[name] = value;
    else missing.push(name === "repos" ? "repositories" : all.find((s) => s.name === name)?.label ?? name);
  }
  return { scope, waiting: missing.join(" and ") };
}

// A setting chosen from what the tracker lists, such as the Jira projects the meta-harness works in, or the
// people the goal's issues can be assigned to. It is a pick, never free text: nothing half-typed reaches the
// goal, or the calls that read its issues. Only when the list of a required one cannot be read is the key
// typed, and then it counts once the field is left. One that is not required can be left empty.
function ChoiceField(props: {
  scheme: string;
  name: string;
  label: string;
  help: string;
  required: boolean;
  scope: Record<string, string>;
  waiting: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const [choices, setChoices] = useState<{ value: string; label: string }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const asked = JSON.stringify(props.scope);
  useEffect(() => {
    let stale = false;
    setChoices(null);
    setError(null);
    if (props.waiting) return;
    api.trackerChoices(props.scheme, props.name, JSON.parse(asked)).then(
      (found) => !stale && setChoices(found),
      (err) => !stale && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      stale = true;
    };
  }, [props.scheme, props.name, asked, props.waiting]);
  const chosen = props.value.trim();
  if (props.waiting) {
    return <Select label={props.label} description={props.help} required={props.required} disabled placeholder={`Choose the ${props.waiting} first`} data={[]} value={null} />;
  }
  if (error && !props.required) {
    return (
      <Stack gap={4}>
        <Select label={props.label} description={props.help} disabled placeholder="Not listed" data={[]} value={null} />
        <Text size="xs" c="orange">The list could not be read, so the goal names nobody here: {error}</Text>
      </Stack>
    );
  }
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
        allowDeselect={!props.required}
        clearable={!props.required}
        disabled={choices === null}
        placeholder={choices === null ? "Loading…" : props.required ? "Choose one" : "Nobody named"}
        nothingFoundMessage="None matches"
        value={known ? chosen : null}
        onChange={(value) => (value ? props.onChange(value) : !props.required && props.onChange(""))}
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
