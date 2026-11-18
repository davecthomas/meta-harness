import { useEffect, useState } from "react";
import { Alert, Autocomplete, Badge, Button, Group, Paper, Stack, Text, Tooltip } from "@mantine/core";
import { api, type Tracker, type TrackerSetting } from "../../api";
import { InlineField } from "../../components/InlineField";
import { ErrorAlert } from "../../components/ui";
import { changed, Reasons, stageOptional, type Staging } from "../config/ConfigViews";

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
  const chosen: string = s.document.default ?? "github";
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
        const settings = setUp[tracker.scheme];
        const isSetUp = settings !== undefined;
        const isDefault = chosen === tracker.scheme;
        return (
          <Paper key={tracker.scheme} p="md" data-testid={`tracker-${tracker.scheme}`}>
            <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
              <Stack gap={4} style={{ flex: "1 1 360px" }}>
                <Group gap="xs">
                  <Text fw={600}>{tracker.title}</Text>
                  {isDefault && <Badge color="indigo" variant="light" tt="none">Default for new goals</Badge>}
                  <Badge color={isSetUp ? "teal" : "gray"} variant="light" tt="none">{isSetUp ? "Set up" : "Not set up"}</Badge>
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
                  {isSetUp && !isDefault && (
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
                    <SettingField staging={s} tracker={tracker} setting={setting} value={settings?.[setting.name]} />
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
    if (setting.required) start[setting.name] = "";
  }
  return start;
}

function SettingField(props: { staging: Staging; tracker: Tracker; setting: TrackerSetting; value: any }) {
  const { staging: s, tracker, setting, value } = props;
  const path = ["trackers", tracker.scheme, setting.name];
  if (setting.kind === "statuses") return <StatusNames staging={s} tracker={tracker} path={path} value={value} />;
  if (setting.kind === "choice") return <ChoiceSetting staging={s} tracker={tracker} setting={setting} path={path} value={value} />;
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

// What the tracker's site has for a setting (GET /v1/trackers/{scheme}/choices/{setting}), read once when the
// page opens: null until it arrives. The list needs the tracker's saved settings and its sign-in, so it can fail.
// groups: the same names under what each belongs to, in the order given, when the tracker groups them.
type Grouped = { group: string; items: string[] }[];

function useChoices(scheme: string, setting: string): { known: string[] | null; groups: Grouped | null; error: string | null } {
  const [known, setKnown] = useState<string[] | null>(null);
  const [groups, setGroups] = useState<Grouped | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let stale = false;
    api.trackerChoices(scheme, setting).then(
      (found) => {
        if (stale) return;
        setKnown((found ?? []).map((c) => c.value));
        const by: Grouped = [];
        for (const c of found ?? []) {
          if (!c.group) continue;
          const at = by.find((g) => g.group === c.group) ?? by[by.push({ group: c.group, items: [] }) - 1];
          at.items.push(c.value);
        }
        setGroups(by.length ? by : null);
      },
      (err) => !stale && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      stale = true;
    };
  }, [scheme, setting]);
  return { known, groups, error };
}

// The site's own spelling of a name, whatever capitals it was typed in; undefined when the site has none.
function onSite(known: string[] | null, name: string): string | undefined {
  return (known ?? []).find((k) => k.toLowerCase() === name.trim().toLowerCase());
}

// A name the tracker's site must have, such as a status or an issue type: picked from the site's own list, so it
// is not mistyped. A mistyped one fails only later, when an issue does not move or cannot be created. A name the
// list lacks is refused, and one already saved is marked. When the list cannot be read (unreadable: why), or is
// empty, the name can still be typed.
function PickedName(props: {
  staging: Staging;
  tracker: Tracker;
  label: string;
  what: string;
  value: string;
  empty: string;
  path: string[];
  known: string[] | null;
  groups?: Grouped | null;
  unreadable: string | null;
  testId: string;
  onCommit: (name: string) => void;
}) {
  const { staging: s, tracker, known } = props;
  const listed = !!known?.length;
  return (
    <div>
      <InlineField
        kind="custom"
        label={props.label}
        value={props.value}
        display={props.value ? <Text size="sm">{props.value}</Text> : undefined}
        empty={<Text size="sm" c="dimmed">{props.empty}</Text>}
        changed={changed(s, props.path)}
        readOnly={s.readOnly}
        check={(draft: string) =>
          listed && draft.trim() && !onSite(known, draft) ? `${tracker.title} has no ${props.what} named ${draft.trim()}: pick one from the list, or leave it empty for the usual` : null
        }
        input={(draft) => (
          <Autocomplete
            aria-label={props.label}
            size="xs"
            w={220}
            placeholder={known === null && !props.unreadable ? "Loading…" : listed ? `Pick ${/^[aeiou]/i.test(props.what) ? "an" : "a"} ${props.what}` : "Type its name"}
            value={draft.value}
            onChange={draft.onChange}
            data={props.groups ?? known ?? []}
            error={draft.invalid}
            maxDropdownHeight={260}
          />
        )}
        onCommit={(v: string) => props.onCommit(onSite(known, v) ?? v.trim())}
      />
      {listed && props.value && !onSite(known, props.value) ? (
        <Text size="xs" c="orange" data-testid={props.testId}>{tracker.title} has no {props.what} named {props.value}</Text>
      ) : null}
    </div>
  );
}

function Unreadable({ what, error }: { what: string; error: string | null }) {
  return error ? <Text size="xs" c="orange">The site's {what} could not be listed, so type each exactly: {error}</Text> : null;
}

// One setting the site lists the choices for, such as Jira's issue type.
function ChoiceSetting(props: { staging: Staging; tracker: Tracker; setting: TrackerSetting; path: string[]; value: any }) {
  const { staging: s, tracker, setting } = props;
  const { known, error } = useChoices(tracker.scheme, setting.name);
  const what = setting.label.toLowerCase();
  return (
    <Stack gap={6}>
      <PickedName
        staging={s}
        tracker={tracker}
        label={`${tracker.title}: ${setting.label}`}
        what={what}
        value={typeof props.value === "string" ? props.value : ""}
        empty={setting.required ? "Not set yet" : "The usual"}
        path={props.path}
        known={known}
        unreadable={error}
        testId={`choice-unknown-${setting.name}`}
        onCommit={(name) => (setting.required ? s.stage(props.path, name) : stageOptional(s, props.path, name || undefined))}
      />
      <Unreadable what={`${what}s`} error={error} />
    </Stack>
  );
}

// The site's own name for each status of ours.
function StatusNames(props: { staging: Staging; tracker: Tracker; path: string[]; value: any }) {
  const { staging: s, tracker, path } = props;
  const names: Record<string, string> = props.value && typeof props.value === "object" ? props.value : {};
  const { known, groups, error } = useChoices(tracker.scheme, "statuses");
  // The projects the goals use, whose own statuses are offered first.
  const projects = (groups ?? []).slice(0, -1).map((g) => g.group);
  // A saved name that only another project's workflow has: it moves no issue of these goals.
  const elsewhere = (name: string) => !!name && projects.length > 0 && groups![groups!.length - 1].items.some((k) => k.toLowerCase() === name.trim().toLowerCase());
  return (
    <Stack gap={6}>
      <Group gap="lg" align="flex-start">
        {STATUSES.map(([status, label]) => (
          <div key={status}>
            <Text size="xs" c="dimmed">{label}</Text>
            <PickedName
              staging={s}
              tracker={tracker}
              label={`${tracker.title}'s status for ${label.toLowerCase()}`}
              what="status"
              value={names[status] ?? ""}
              empty="The usual"
              path={[...path, status]}
              known={known}
              groups={groups}
              unreadable={error}
              testId={`status-unknown-${status}`}
              onCommit={(name) => {
                const next = { ...names, [status]: name };
                if (!name) delete next[status];
                stageOptional(s, path, Object.keys(next).length ? next : undefined);
              }}
            />
            {elsewhere(names[status] ?? "") && (
              <Text size="xs" c="orange" data-testid={`status-elsewhere-${status}`}>Not a status of {projects.join(" or ")}</Text>
            )}
          </div>
        ))}
      </Group>
      {projects.length > 0 && (
        <Text size="xs" c="dimmed">The statuses of {projects.join(", ")}, the {projects.length === 1 ? "project your goals use" : "projects your goals use"}, are listed first.</Text>
      )}
      <Unreadable what="status names" error={error} />
    </Stack>
  );
}
