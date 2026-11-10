import { useEffect, useState } from "react";
import { Badge, Button, Checkbox, Group, NumberInput, Paper, SegmentedControl, Stack, Switch, Text, Title, useMantineColorScheme } from "@mantine/core";
import { IconDeviceFloppy } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import { api, type UserSettings } from "../api";
import { useUnsavedChanges } from "../components/UnsavedChanges";
import { ErrorAlert, PageHeader } from "../components/ui";

// The signed-in user's own settings: what New goal starts from, connected trackers, appearance and notifications.
export function SettingsPage() {
  const [saved, setSaved] = useState<UserSettings | null>(null);
  const [draft, setDraft] = useState<UserSettings | null>(null);
  const [repos, setRepos] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const { setColorScheme } = useMantineColorScheme();

  useEffect(() => {
    api.settings().then(
      (s) => {
        setSaved(s);
        setDraft(s);
      },
      (err) => setError(err instanceof Error ? err.message : String(err)),
    );
    api.repos().then((r) => setRepos(r.map((x) => x.name)), () => setRepos([]));
  }, []);

  async function save(): Promise<boolean> {
    if (!draft) return false;
    setSaving(true);
    try {
      const stored = await api.saveSettings(draft);
      setSaved(stored);
      setDraft(stored);
      setColorScheme(stored.appearance === "system" ? "auto" : stored.appearance);
      notifications.show({ color: "teal", title: "Settings saved", message: "New goal starts from them." });
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return false;
    } finally {
      setSaving(false);
    }
  }

  const unsaved = useUnsavedChanges({
    dirty: JSON.stringify(draft) !== JSON.stringify(saved),
    describe: "your settings",
    save,
    discard: () => setDraft(saved),
  });

  if (!draft) return <ErrorAlert error={error} />;
  const planner = draft.planner;
  const setPlanner = (fields: Partial<UserSettings["planner"]>) => setDraft({ ...draft, planner: { ...planner, ...fields } });

  return (
    <>
      <PageHeader title="Settings" description="Yours alone: they follow you to any browser you sign in on." />
      <ErrorAlert error={error} />
      {unsaved.element}
      <Stack gap="lg" maw={720}>
        <Paper p="md" withBorder>
          <Title order={4} mb="sm">Planner defaults</Title>
          <Text size="sm" c="dimmed" mb="md">New goal starts from these.</Text>
          <Stack gap="md">
            <Checkbox.Group label="Repositories" value={planner.repos} onChange={(v) => setPlanner({ repos: v })}>
              <Stack gap="xs" mt="xs">{repos.map((r) => <Checkbox key={r} value={r} label={r} />)}</Stack>
            </Checkbox.Group>
            <SegmentedControl
              value={planner.modality}
              onChange={(v) => setPlanner({ modality: v as UserSettings["planner"]["modality"] })}
              data={[{ value: "fix", label: "Fix" }, { value: "feature", label: "Feature" }, { value: "new-system", label: "New system" }]}
              w={320}
            />
            <Group grow>
              <NumberInput label="Latitude" description="0 to 1. Empty: the modality's default." min={0} max={1} step={0.25} decimalScale={2} value={planner.latitude ?? ""} onChange={(v) => setPlanner({ latitude: v === "" ? null : Number(v) })} />
              <NumberInput label="Budget" prefix="$" min={0} decimalScale={2} value={planner.max_usd ?? ""} onChange={(v) => setPlanner({ max_usd: v === "" ? null : String(v) })} />
            </Group>
            <Switch label="Merge pull requests without me" checked={planner.auto_merge} onChange={(e) => setPlanner({ auto_merge: e.currentTarget.checked })} />
          </Stack>
        </Paper>
        <Paper p="md" withBorder>
          <Title order={4} mb="sm">Connected trackers</Title>
          <Group justify="space-between">
            <Text size="sm">GitHub, through the deployment's GitHub App</Text>
            <Badge color="teal" variant="light">Connected</Badge>
          </Group>
        </Paper>
        <Paper p="md" withBorder>
          <Title order={4} mb="sm">Appearance</Title>
          <SegmentedControl
            value={draft.appearance}
            onChange={(v) => setDraft({ ...draft, appearance: v as UserSettings["appearance"] })}
            data={[{ value: "light", label: "Light" }, { value: "dark", label: "Dark" }, { value: "system", label: "System" }]}
          />
        </Paper>
        <Paper p="md" withBorder>
          <Title order={4} mb="sm">Notifications</Title>
          <Switch
            label="Browser notifications for what needs me"
            description="The browser asks once to allow them."
            checked={draft.notifications}
            onChange={(e) => {
              const on = e.currentTarget.checked;
              if (on && typeof Notification !== "undefined" && Notification.permission === "default") void Notification.requestPermission();
              setDraft({ ...draft, notifications: on });
            }}
          />
        </Paper>
        <Group justify="flex-end">
          <Button leftSection={<IconDeviceFloppy size={16} />} loading={saving} disabled={JSON.stringify(draft) === JSON.stringify(saved)} onClick={() => void save()}>
            Save settings
          </Button>
        </Group>
      </Stack>
    </>
  );
}
