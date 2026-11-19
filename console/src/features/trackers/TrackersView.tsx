import { useEffect, useState } from "react";
import { Alert, Autocomplete, Badge, Button, Group, Paper, Stack, Text, Tooltip } from "@mantine/core";
import { getIn } from "../config/staging";
import { api, type Tracker, type TrackerSetting } from "../../api";
import { InlineField } from "../../components/InlineField";
import { PickedName, Unreadable } from "../../components/PickedName";
import { ErrorAlert } from "../../components/ui";
import { changed, Reasons, stageOptional, type Staging } from "../config/ConfigViews";
import { OWN } from "./refs";

// The Issue trackers configuration: every tracker the meta-harness supports, as its adapter describes it. One
// is set up by filling in its settings, and one of those set up is the default, which a new goal uses unless it
// names another. A goal keeps the tracker it was created with. A sign-in is never here: it is the deployment's
// secrets, and each tracker says whether its sign-in is there.

const STATUSES: [string, string][] = [
  ["todo", "To do"],
  ["in_progress", "In progress"],
  ["in_review", "In review"],
  ["done", "Done"],
];

export function TrackersView({ staging: s }: { staging: Staging }) {
  const [supported, setSupported] = useState<Tracker[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.trackers().then((found) => setSupported(found.trackers), (err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  if (error) return <ErrorAlert error={`The supported trackers could not be read: ${error}`} />;
  if (!supported) return null;
  const setUp: Record<string, any> = s.document.trackers ?? {};
  // With no tracker set up, new goals use none.
  const chosen: string = Object.keys(setUp).length ? (s.document.default ?? "github") : OWN;
  return (
    <Stack gap="md">
      <Reasons reasons={s.reasons("default")} />
      <Paper p="md" data-testid="tracker-label">
        <Stack gap={4}>
          <Text fw={600}>Label on the meta-harness's issues</Text>
          <InlineField
            kind="text"
            label="Label on the meta-harness's issues"
            value={s.document.label ?? ""}
            monospace
            empty={<Text size="sm" ff="monospace">ai:meta-harness</Text>}
            placeholder="ai:meta-harness"
            changed={changed(s, ["label"])}
            reasons={s.reasons("label")}
            readOnly={s.readOnly}
            onCommit={(v) => stageOptional(s, ["label"], v.trim())}
          />
          <Text size="xs" c="dimmed">
            Every issue the meta-harness creates, comments on or moves gets this label, in every tracker, so its work can be told apart and listed. No spaces. Empty: ai:meta-harness.
          </Text>
        </Stack>
      </Paper>
      {supported.map((tracker) => {
        // The choice of no tracker has nothing to set up or remove: it is only ever the default, or not.
        const builtIn = !!tracker.built_in;
        const settings = setUp[tracker.scheme];
        const isSetUp = builtIn || settings !== undefined;
        const isDefault = chosen === tracker.scheme;
        return (
          <Paper key={tracker.scheme} p="md" data-testid={`tracker-${tracker.scheme}`}>
            <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
              <Stack gap={4} style={{ flex: "1 1 360px" }}>
                <Group gap="xs">
                  <Text fw={600}>{tracker.title}</Text>
                  {isDefault && <Badge color="indigo" variant="light" tt="none">Default for new goals</Badge>}
                  {!builtIn && <Badge color={isSetUp ? "teal" : "gray"} variant="light" tt="none">{isSetUp ? "Set up" : "Not set up"}</Badge>}
                  {isSetUp && tracker.credentials.length > 0 && (
                    <Badge color={tracker.signed_in ? "teal" : "orange"} variant="light" tt="none">
                      {tracker.signed_in ? "Signed in" : "Sign-in missing"}
                    </Badge>
                  )}
                </Group>
                <Text size="sm" c="dimmed">{tracker.description}</Text>
              </Stack>
              {!s.readOnly && (
                <Group gap="xs">
                  {!isSetUp && (
                    <Button size="xs" variant="light" onClick={() => s.stage(["trackers", tracker.scheme], blank(tracker))}>
                      Set up {tracker.title}
                    </Button>
                  )}
                  {isSetUp && !isDefault && (
                    <Button size="xs" variant="light" onClick={() => s.stage(["default"], tracker.scheme)}>Make default</Button>
                  )}
                  {isSetUp && !isDefault && !builtIn && (
                    <Tooltip label="Goals that use it keep their issues, and stop reading new ones from it" multiline w={260}>
                      <Button size="xs" variant="subtle" color="red" onClick={() => s.stage(["trackers", tracker.scheme], undefined)}>
                        Remove
                      </Button>
                    </Tooltip>
                  )}
                </Group>
              )}
            </Group>
            {isSetUp && tracker.credentials.length > 0 && !tracker.signed_in && (
              <Alert color="orange" mt="sm" p="xs" title={`${tracker.title} has no sign-in yet`}>
                <Text size="sm">
                  Missing: <Text span ff="monospace">{tracker.missing_credentials.join(", ")}</Text>. A sign-in is a secret, so it
                  is not set here: add each as a secret of the deployment (Secret Manager), then deploy. Until then a goal that
                  uses {tracker.title} reads no issues, and says so.
                </Text>
              </Alert>
            )}
            <Reasons reasons={s.reasons(`tracker:${tracker.scheme}`)} />
            {isSetUp && tracker.goal_settings.length > 0 && (
              <Text size="sm" c="dimmed" mt="sm">
                Each goal that uses {tracker.title} sets its own when it is created: {tracker.goal_settings.map((g) => g.label).join("; ")}.
              </Text>
            )}
            {isSetUp && tracker.settings.length > 0 && (
              <Stack gap="sm" mt="md">
                {tracker.settings.map((setting) => (
                  <div key={setting.name}>
                    <Text size="sm" fw={500}>
                      {setting.label}
                      {setting.required && <Text span c="red"> *</Text>}
                    </Text>
                    <Text size="xs" c="dimmed" mb={4}>{setting.help}</Text>
                    <SettingField staging={s} tracker={tracker} setting={setting} path={["trackers", tracker.scheme, setting.name]} value={settings?.[setting.name]} />
                  </div>
                ))}
              </Stack>
            )}
          </Paper>
        );
      })}
    </Stack>
  );
}

// A tracker's settings as they start: each required one empty, for the operator to fill in.
function blank(tracker: Tracker): Record<string, unknown> {
  const start: Record<string, unknown> = {};
  for (const setting of tracker.settings) {
    if (setting.required) start[setting.name] = setting.kind === "entries" ? {} : "";
  }
  return start;
}

// One setting, at its place in the document. scope: what it belongs to, such as the project whose setting it
// is, which the tracker's list of choices for it is asked by.
function SettingField(props: { staging: Staging; tracker: Tracker; setting: TrackerSetting; path: string[]; value: any; scope?: Record<string, string> }) {
  const { staging: s, tracker, setting, value, path } = props;
  if (setting.kind === "entries") return <EntriesSetting staging={s} tracker={tracker} setting={setting} path={path} value={value} />;
  if (setting.kind === "entry_of") return <EntryOfSetting staging={s} tracker={tracker} setting={setting} path={path} value={value} />;
  if (setting.kind === "statuses") return <StatusNames staging={s} tracker={tracker} path={path} value={value} scope={props.scope} />;
  if (setting.kind === "choice") return <ChoiceSetting staging={s} tracker={tracker} setting={setting} path={path} value={value} scope={props.scope} />;
  return (
    <InlineField
      kind="text"
      label={`${tracker.title}: ${setting.label}`}
      value={typeof value === "string" ? value : ""}
      empty={<Text size="sm" c="dimmed">{setting.required ? "Not set yet" : "The usual"}</Text>}
      changed={changed(s, path)}
      readOnly={s.readOnly}
      onCommit={(v) => (setting.required ? s.stage(path, v.trim()) : stageOptional(s, path, v.trim()))}
    />
  );
}

// What the tracker's site has for a setting (GET /v1/trackers/{scheme}/choices/{setting}), read when the page
// opens: null until it arrives. labels: each one's name, for a choice shown by more than its value (a project's
// name beside its key). The list needs the tracker's saved settings and its sign-in, so it can fail.
function useChoices(scheme: string, setting: string, scope?: Record<string, string>): { known: string[] | null; labels: Record<string, string>; error: string | null } {
  const [known, setKnown] = useState<string[] | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const asked = JSON.stringify(scope ?? {});
  useEffect(() => {
    let stale = false;
    setKnown(null);
    setError(null);
    api.trackerChoices(scheme, setting, JSON.parse(asked)).then(
      (found) => {
        if (stale) return;
        setKnown((found ?? []).map((c) => c.value));
        setLabels(Object.fromEntries((found ?? []).map((c) => [c.value, c.label])));
      },
      (err) => !stale && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      stale = true;
    };
  }, [scheme, setting, asked]);
  return { known, labels, error };
}

// The list's own spelling of a name, whatever capitals it was typed in; undefined when the list has none.
function onSite(known: string[] | null, name: string): string | undefined {
  return (known ?? []).find((k) => k.toLowerCase() === name.trim().toLowerCase());
}

// Whose list a scoped setting's choices are: the project it belongs to, else the tracker.
function whose(tracker: Tracker, scope?: Record<string, string>): string {
  return Object.values(scope ?? {})[0] ?? tracker.title;
}

// One setting the tracker lists the choices for, such as a project's issue type.
function ChoiceSetting(props: { staging: Staging; tracker: Tracker; setting: TrackerSetting; path: string[]; value: any; scope?: Record<string, string> }) {
  const { staging: s, tracker, setting } = props;
  const { known, error } = useChoices(tracker.scheme, setting.name, props.scope);
  const what = setting.label.toLowerCase();
  const where = whose(tracker, props.scope);
  return (
    <Stack gap={6}>
      <PickedName
        label={`${where}: ${setting.label}`}
        what={what}
        where={where}
        value={typeof props.value === "string" ? props.value : ""}
        empty={setting.required ? "Not set yet" : "The usual"}
        changed={changed(s, props.path)}
        readOnly={s.readOnly}
        known={known}
        unreadable={error}
        testId={`choice-unknown-${setting.name}`}
        onCommit={(name) => (setting.required ? s.stage(props.path, name) : stageOptional(s, props.path, name || undefined))}
      />
      <Unreadable what={`${what}s`} error={error} />
    </Stack>
  );
}

// A project's own name for each status of ours, each picked from the statuses of that project's workflows.
function StatusNames(props: { staging: Staging; tracker: Tracker; path: string[]; value: any; scope?: Record<string, string> }) {
  const { staging: s, tracker, path } = props;
  const names: Record<string, string> = props.value && typeof props.value === "object" ? props.value : {};
  const { known, error } = useChoices(tracker.scheme, "statuses", props.scope);
  const where = whose(tracker, props.scope);
  return (
    <Stack gap={6}>
      <Group gap="lg" align="flex-start">
        {STATUSES.map(([status, label]) => (
          <div key={status}>
            <Text size="xs" c="dimmed">{label}</Text>
            <PickedName
              label={`${where}'s status for ${label.toLowerCase()}`}
              what="status"
              where={where}
              value={names[status] ?? ""}
              empty="The usual"
              changed={changed(s, [...path, status])}
              readOnly={s.readOnly}
              known={known}
              unreadable={error}
              testId={`status-unknown-${status}`}
              onCommit={(name) => {
                const next = { ...names, [status]: name };
                if (!name) delete next[status];
                stageOptional(s, path, Object.keys(next).length ? next : undefined);
              }}
            />
          </div>
        ))}
      </Group>
      <Unreadable what="status names" error={error} />
    </Stack>
  );
}

// A list the operator adds to, each entry picked from what the tracker lists and holding settings of its own:
// the Jira projects the meta-harness works in, each with its issue type and status names. An entry is added by
// picking it, never by typing a key the tracker does not have. The first added becomes the default, since one
// is required; the default cannot be removed until another is.
function EntriesSetting(props: { staging: Staging; tracker: Tracker; setting: TrackerSetting; path: string[]; value: any }) {
  const { staging: s, tracker, setting, path } = props;
  const entries: Record<string, any> = props.value && typeof props.value === "object" ? props.value : {};
  const { known, labels, error } = useChoices(tracker.scheme, setting.name);
  const [adding, setAdding] = useState("");
  const [refused, setRefused] = useState<string | null>(null);
  const one = setting.entry || "entry";
  // The setting that names one of these entries as the default, and what it holds now.
  const picks = tracker.settings.find((other) => other.kind === "entry_of" && other.of === setting.name);
  const base = path.slice(0, -1);
  const chosen = picks ? getIn(s.document, [...base, picks.name]) : undefined;
  // Settings for the whole tracker from before entries were listed, which a first entry takes over.
  const before = Object.fromEntries((setting.settings ?? []).map((sub) => [sub.name, getIn(s.document, [...base, sub.name])]).filter(([, v]) => v !== undefined));

  function add() {
    const typed = adding.trim();
    const key = known?.find((k) => labels[k] === typed) ?? onSite(known, typed) ?? typed.toUpperCase();
    if (!key) return;
    if (entries[key] !== undefined) return setRefused(`${key} is already listed`);
    if (known?.length && !known.includes(key)) return setRefused(`${tracker.title} has no ${one} ${key} that its sign-in can see: pick one from the list`);
    const first = Object.keys(entries).length === 0;
    s.stage([...path, key], first ? before : {});
    if (first) for (const name of Object.keys(before)) s.stage([...base, name], undefined);
    if (picks && !chosen) s.stage([...base, picks.name], key);
    setAdding("");
    setRefused(null);
  }

  return (
    <Stack gap="sm">
      {Object.keys(before).length > 0 && (
        <Text size="xs" c="orange" data-testid={`entries-before-${setting.name}`}>
          This setup is from before {setting.label.toLowerCase()} were listed: its {(setting.settings ?? []).filter((sub) => before[sub.name] !== undefined).map((sub) => sub.label.toLowerCase()).join(" and ")} apply to every {one}. The first {one} you add takes them over.
        </Text>
      )}
      {Object.keys(entries).length === 0 && <Text size="sm" c="dimmed">None yet. Add the first below.</Text>}
      {Object.entries(entries).map(([key, entry]) => (
        <Paper key={key} p="sm" withBorder data-testid={`entry-${key}`}>
          <Group justify="space-between" align="center" mb="xs">
            <Group gap="xs">
              <Text fw={600} size="sm">{labels[key] ?? key}</Text>
              {chosen === key && <Badge size="sm" variant="light" tt="none">Default</Badge>}
              {known?.length && !known.includes(key) ? <Text size="xs" c="orange">{tracker.title} has no {one} {key} that its sign-in can see</Text> : null}
            </Group>
            {!s.readOnly && (
              <Tooltip label={chosen === key ? `The default ${one} stays: make another the default first` : `Stop working in ${key}. A goal that uses it must be closed first.`}>
                <Button size="xs" variant="subtle" color="red" disabled={chosen === key} onClick={() => s.stage([...path, key], undefined)} aria-label={`Remove ${key}`}>
                  Remove
                </Button>
              </Tooltip>
            )}
          </Group>
          <Stack gap="sm">
            {(setting.settings ?? []).map((sub) => (
              <div key={sub.name}>
                <Text size="sm" fw={500}>{sub.label}</Text>
                <Text size="xs" c="dimmed" mb={4}>{sub.help}</Text>
                <SettingField staging={s} tracker={tracker} setting={sub} path={[...path, key, sub.name]} value={entry?.[sub.name]} scope={{ [one]: key }} />
              </div>
            ))}
          </Stack>
        </Paper>
      ))}
      {!s.readOnly && (
        <Stack gap={4}>
          <Group gap="xs" align="flex-end">
            <Autocomplete
              aria-label={`Add a ${one}`}
              size="xs"
              w={320}
              placeholder={known === null && !error ? "Loading…" : known?.length ? `Pick a ${one} to add` : `Type the ${one}'s key`}
              value={adding}
              onChange={(v) => {
                setAdding(v);
                setRefused(null);
              }}
              data={(known ?? []).filter((k) => entries[k] === undefined).map((k) => labels[k] ?? k)}
              error={!!refused}
              maxDropdownHeight={260}
              onKeyDown={(e) => {
                if (e.key === "Enter") add();
              }}
            />
            <Button size="xs" variant="light" onClick={add} disabled={!adding.trim()}>Add {one}</Button>
          </Group>
          {refused && <Text size="xs" c="red" role="alert">{refused}</Text>}
          <Unreadable what={`${one}s`} error={error} />
        </Stack>
      )}
    </Stack>
  );
}

// One of the entries of another setting, such as the default project among the projects listed. It is chosen
// from them, so it cannot name one that is not there, and it is never cleared: one is required.
function EntryOfSetting(props: { staging: Staging; tracker: Tracker; setting: TrackerSetting; path: string[]; value: any }) {
  const { staging: s, tracker, setting, path } = props;
  const listed = Object.keys(getIn(s.document, [...path.slice(0, -1), setting.of ?? ""]) ?? {});
  const value = typeof props.value === "string" && props.value ? props.value : null;
  const data = value && !listed.includes(value) ? [...listed, value] : listed;
  return (
    <Stack gap={4}>
      <InlineField
        kind="select"
        label={`${tracker.title}: ${setting.label}`}
        value={value}
        data={data}
        empty={<Text size="sm" c={setting.required ? "red" : "dimmed"}>{listed.length ? "Not chosen yet" : "Add one above first"}</Text>}
        changed={changed(s, path)}
        readOnly={s.readOnly}
        onCommit={(v) => (v ? s.stage(path, v) : undefined)}
      />
      {value && !listed.includes(value) && <Text size="xs" c="orange">{value} is not one of those listed: choose one that is</Text>}
    </Stack>
  );
}
