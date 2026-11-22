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
  // The goal's repos, owner/name: the scope of a list that depends on them, such as GitHub's assignable people.
  repos?: string[];
  // Which of the tracker's settings this part of the form shows; all of them when left out. The form shows
  // where the issues are first, and what most goals leave alone further down.
  shows?: (setting: Tracker["goal_settings"][number]) => boolean;
  // Leave out the tracker itself: another part of the form has it.
  settingsOnly?: boolean;
}) {
  const chosen = props.trackers.find((t) => t.scheme === props.tracker);
  const shown = (chosen?.goal_settings ?? []).filter((s) => !props.shows || props.shows(s));
  if (props.settingsOnly && !shown.length) return null;
  // Whether the tracker asks a goal for a project, which then scopes its other lists (Jira's assignable people,
  // and the epics a goal's parent is picked from).
  const asksProject = (chosen?.goal_settings ?? []).some((s) => s.name === "project");
  const project = (props.settings.project ?? "").trim();
  const repos = (props.repos ?? []).join(",");
  // A choice is set, or left out when emptied, as a goal that never had it.
  function choose(name: string, value: string) {
    const { [name]: _gone, ...rest } = props.settings;
    props.onSettings(value ? { ...rest, [name]: value } : rest);
  }
  return (
    <Stack gap="lg">
      {props.settingsOnly ? null : props.trackers.length > 1 ? (
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
      {!props.settingsOnly && chosen && !chosen.signed_in && (
        <Text size="sm" c="orange" data-testid="tracker-signed-out">
          {chosen.title} has no sign-in yet, so a goal that uses it reads no issues until an admin adds one (Configuration, Issue trackers says which is missing).
        </Text>
      )}
      {shown.map((setting) =>
        setting.kind === "choice" ? (
          <ChoiceField
            key={`${chosen!.scheme}:${setting.name}`}
            scheme={chosen!.scheme}
            name={setting.name}
            label={setting.label}
            help={setting.help}
            required={setting.required}
            value={props.settings[setting.name] ?? ""}
            onChange={(value) => choose(setting.name, value)}
            // The project itself is listed whole. Every other list is the goal's project's, or its repos'.
            scope={setting.name === "project" ? {} : { ...(project ? { project } : {}), ...(repos ? { repos } : {}) }}
            waiting={setting.name !== "project" && asksProject && !project ? "Choose the project first" : undefined}
            keyCase={setting.name === "project"}
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

// A setting chosen from what the tracker lists, such as the Jira projects the meta-harness works in, or the
// people a goal's issues can be assigned to. It is a pick, never free text: nothing half-typed reaches the goal,
// or the calls that read its issues. Only when the list cannot be read is the value typed, and then it counts
// once the field is left. scope: what the list belongs to (the goal's project or repos); waiting: why the list
// is not asked for yet. A setting that is not required can be emptied again.
export function ChoiceField(props: {
  scheme: string;
  name: string;
  label: string;
  help: string;
  required: boolean;
  value: string;
  onChange: (value: string) => void;
  scope?: Record<string, string>;
  waiting?: string;
  keyCase?: boolean;
}) {
  const [choices, setChoices] = useState<{ value: string; label: string }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The scope as text, so a new object with the same values does not ask again.
  const scope = JSON.stringify(props.scope ?? {});
  useEffect(() => {
    let stale = false;
    setChoices(null);
    setError(null);
    if (props.waiting) return;
    api.trackerChoices(props.scheme, props.name, JSON.parse(scope)).then(
      (found) => !stale && setChoices(found),
      (err) => !stale && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      stale = true;
    };
  }, [props.scheme, props.name, scope, props.waiting]);
  const chosen = props.value.trim();
  if (error) {
    return (
      <Stack gap={4}>
        <TypedKey label={props.label} help={props.help} required={props.required} value={props.value} onChange={props.onChange} keyCase={props.keyCase} />
        <Text size="xs" c="orange">The list could not be read, so type {props.keyCase ? "the key" : "it"} exactly: {error}</Text>
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
        placeholder={props.waiting ?? (choices === null ? "Loading…" : choices.length === 0 ? "None to choose from" : props.required ? "Choose one" : "Choose one, or leave empty")}
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

// A value typed by hand, which counts once the field is left. keyCase: a key, in capitals as keys are.
function TypedKey(props: { label: string; help: string; required: boolean; value: string; onChange: (value: string) => void; keyCase?: boolean }) {
  const [draft, setDraft] = useState(props.value);
  return (
    <TextInput
      label={props.label}
      description={props.help}
      required={props.required}
      value={draft}
      onChange={(e) => setDraft(props.keyCase ? e.currentTarget.value.toUpperCase() : e.currentTarget.value)}
      onBlur={() => draft.trim() !== props.value && props.onChange(draft.trim())}
    />
  );
}
