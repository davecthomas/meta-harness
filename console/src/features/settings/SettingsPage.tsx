import { useEffect, useRef, useState } from "react";
import { Badge, Group, Paper, Text, Title, useMantineColorScheme } from "@mantine/core";
import { api, type UserSettings } from "../../api";
import { InlineField } from "../../components/InlineField";
import { ErrorAlert, Facts, PageHeader } from "../../components/ui";

const MODALITIES = [
  { value: "fix", label: "Fix" },
  { value: "feature", label: "Feature" },
  { value: "new-system", label: "New system" },
];
const APPEARANCES = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
];

// The signed-in user's own settings: what New goal starts from, connected trackers, appearance and notifications.
// Each field edits in place and saves when it is committed.
export function SettingsPage() {
  const [settings, setSettings] = useState<UserSettings | null>(null);
  // The settings as last stored, so two quick commits each build on the other.
  const latest = useRef<UserSettings | null>(null);
  // Saves run one at a time, each after the one before it has stored, so a second commit made while the
  // first is in flight builds on the first's result instead of the settings both started from.
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const [repos, setRepos] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const { setColorScheme } = useMantineColorScheme();

  useEffect(() => {
    api.settings().then(
      (s) => {
        latest.current = s;
        setSettings(s);
      },
      (err) => setError(err instanceof Error ? err.message : String(err)),
    );
    api.repos().then((r) => setRepos(r.map((x) => x.name)), () => setRepos([]));
  }, []);

  // Saves one field. A failure is the field's refusal: it stays open with the reason.
  function save(change: (s: UserSettings) => UserSettings): Promise<string | null> {
    const run = async (): Promise<string | null> => {
      if (!latest.current) return "Your settings have not loaded";
      try {
        const stored = await api.saveSettings(change(latest.current));
        latest.current = stored;
        setSettings(stored);
        return null;
      } catch (err) {
        return err instanceof Error ? err.message : String(err);
      }
    };
    const next = queue.current.then(run, run);
    queue.current = next;
    return next;
  }
  const savePlanner = (fields: Partial<UserSettings["planner"]>) => save((s) => ({ ...s, planner: { ...s.planner, ...fields } }));

  if (!settings) return <ErrorAlert error={error} />;
  const planner = settings.planner;

  return (
    <>
      <PageHeader title="Settings" description="Yours alone: they follow you to any browser you sign in on. Each saves as you change it." />
      <ErrorAlert error={error} />
      <Paper p="md" withBorder maw={720} mb="lg">
        <Title order={4} mb="sm">Planner defaults</Title>
        <Text size="sm" c="dimmed" mb="md">New goal starts from these.</Text>
        <Facts
          items={[
            ["Repositories", <InlineField kind="checkboxes" label="Repositories" value={planner.repos} data={repos} empty="No repository is registered" showSaved onCommit={(v) => savePlanner({ repos: v })} />],
            ["Modality", <InlineField kind="select" label="Modality" value={planner.modality} data={MODALITIES} showSaved onCommit={(v) => savePlanner({ modality: (v ?? "fix") as UserSettings["planner"]["modality"] })} />],
            [
              "Latitude",
              <InlineField kind="number" label="Latitude" value={planner.latitude} min={0} max={1} step={0.25} decimalScale={2} allowEmpty empty="The modality's default" placeholder="0 to 1" showSaved onCommit={(v) => savePlanner({ latitude: v })} />,
            ],
            [
              "Budget",
              <InlineField kind="number" label="Budget" value={planner.max_usd === null ? null : Number(planner.max_usd)} min={0} decimalScale={2} prefix="$" allowEmpty empty="None" showSaved onCommit={(v) => savePlanner({ max_usd: v === null ? null : String(v) })} />,
            ],
            ["Auto-merge", <InlineField kind="switch" label="Auto-merge" switchLabel="Merge pull requests without me" value={planner.auto_merge} showSaved onCommit={(v) => savePlanner({ auto_merge: v })} />],
          ]}
        />
      </Paper>
      <Paper p="md" withBorder maw={720} mb="lg">
        <Title order={4} mb="sm">Connected trackers</Title>
        <Group justify="space-between">
          <Text size="sm">GitHub, through the deployment's GitHub App</Text>
          <Badge color="teal" variant="light">Connected</Badge>
        </Group>
      </Paper>
      <Paper p="md" withBorder maw={720} mb="lg">
        <Title order={4} mb="sm">Appearance and notifications</Title>
        <Facts
          items={[
            [
              "Appearance",
              <InlineField
                kind="select"
                label="Appearance"
                value={settings.appearance}
                data={APPEARANCES}
                showSaved
                onCommit={async (v) => {
                  const appearance = (v ?? "system") as UserSettings["appearance"];
                  const refused = await save((s) => ({ ...s, appearance }));
                  if (!refused) setColorScheme(appearance === "system" ? "auto" : appearance);
                  return refused;
                }}
              />,
            ],
            [
              "Notifications",
              <InlineField
                kind="switch"
                label="Notifications"
                switchLabel="Browser notifications for what needs me"
                value={settings.notifications}
                showSaved
                onCommit={(on) => {
                  if (on && typeof Notification !== "undefined" && Notification.permission === "default") void Notification.requestPermission();
                  return save((s) => ({ ...s, notifications: on }));
                }}
              />,
            ],
          ]}
        />
        <Text size="xs" c="dimmed" mt="xs">The browser asks once to allow notifications.</Text>
      </Paper>
    </>
  );
}
