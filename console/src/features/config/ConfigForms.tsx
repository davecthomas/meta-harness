import { ActionIcon, Alert, Button, Card, Group, NumberInput, Select, Stack, TagsInput, Text, TextInput, Textarea } from "@mantine/core";
import { IconArrowDown, IconArrowUp, IconPlus, IconTrash } from "@tabler/icons-react";

const THINKING = ["off", "minimal", "low", "medium", "high", "max"];

// The save check's reasons that name a tier or a profile, so each shows beside what it is about.
export function reasonsFor(reasons: string[], prefix: string): string[] {
  return reasons.filter((r) => r.startsWith(prefix));
}

// A candidate's parts: harness:<profile>/<engine>:<model>.
export function splitCandidate(candidate: string): { profile: string; model: string } {
  const [profile, ...rest] = candidate.replace(/^harness:/, "").split("/");
  return { profile: profile ?? "", model: rest.join("/") };
}

export function joinCandidate(profile: string, model: string): string {
  return `harness:${profile}/${model}`;
}

function move<T>(items: T[], from: number, to: number): T[] {
  if (to < 0 || to >= items.length) return items;
  const next = [...items];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

function Reasons({ reasons }: { reasons: string[] }) {
  if (!reasons.length) return null;
  return (
    <Alert color="red" p="xs">
      <Stack gap={2}>{reasons.map((r) => <Text key={r} size="xs">{r}</Text>)}</Stack>
    </Alert>
  );
}

type Tier = { name: string; description?: string; models?: string[]; thinking?: string };

// The router's tiers, in order: each with its description, thinking level and candidates, which are a
// harness profile and a model each, in the order the run loop tries them.
export function TiersForm(props: {
  document: Record<string, any>;
  onChange: (document: Record<string, any>) => void;
  profiles: string[];
  models: string[];
  reasons: string[];
}) {
  const tiers: Tier[] = props.document.tiers ?? [];
  const setTiers = (next: Tier[]) => props.onChange({ ...props.document, tiers: next });
  const setTier = (index: number, tier: Tier) => setTiers(tiers.map((t, i) => (i === index ? tier : t)));
  return (
    <Stack gap="md">
      {tiers.map((tier, index) => {
        const models = tier.models ?? [];
        const setModels = (next: string[]) => setTier(index, { ...tier, models: next });
        return (
          <Card key={index} withBorder padding="md" data-testid={`tier-${tier.name}`}>
            <Stack gap="sm">
              <Group grow align="flex-end">
                <TextInput label="Tier" value={tier.name} onChange={(e) => setTier(index, { ...tier, name: e.currentTarget.value })} />
                <Select label="Thinking" clearable value={tier.thinking ?? null} data={THINKING} onChange={(v) => setTier(index, { ...tier, thinking: v ?? undefined })} />
              </Group>
              <Textarea label="Description" description="The classifier reads it to place a task in this tier." autosize minRows={2} value={tier.description ?? ""} onChange={(e) => setTier(index, { ...tier, description: e.currentTarget.value })} />
              <Text size="xs" fw={600} c="dimmed" tt="uppercase">Candidates, in order</Text>
              {models.map((candidate, i) => {
                const { profile, model } = splitCandidate(candidate);
                return (
                  <Group key={i} gap="xs" wrap="nowrap" align="flex-end">
                    <Text size="sm" c="dimmed" w={18}>{i + 1}.</Text>
                    <Select aria-label={`Profile of candidate ${i + 1} in ${tier.name}`} data={props.profiles} value={profile || null} onChange={(v) => setModels(models.map((m, j) => (j === i ? joinCandidate(v ?? "", model) : m)))} w={160} />
                    <Select
                      aria-label={`Model of candidate ${i + 1} in ${tier.name}`}
                      data={props.models.includes(model) || !model ? props.models : [model, ...props.models]}
                      value={model || null}
                      searchable
                      onChange={(v) => setModels(models.map((m, j) => (j === i ? joinCandidate(profile, v ?? "") : m)))}
                      style={{ flex: 1 }}
                    />
                    <ActionIcon variant="subtle" aria-label="Move up" disabled={i === 0} onClick={() => setModels(move(models, i, i - 1))}><IconArrowUp size={16} /></ActionIcon>
                    <ActionIcon variant="subtle" aria-label="Move down" disabled={i === models.length - 1} onClick={() => setModels(move(models, i, i + 1))}><IconArrowDown size={16} /></ActionIcon>
                    <ActionIcon variant="subtle" color="red" aria-label="Remove candidate" onClick={() => setModels(models.filter((_, j) => j !== i))}><IconTrash size={16} /></ActionIcon>
                  </Group>
                );
              })}
              <Group justify="space-between">
                <Button size="xs" variant="light" leftSection={<IconPlus size={14} />} onClick={() => setModels([...models, joinCandidate(props.profiles[0] ?? "", "")])}>
                  Add candidate
                </Button>
                <Group gap="xs">
                  <Button size="xs" variant="subtle" disabled={index === 0} onClick={() => setTiers(move(tiers, index, index - 1))}>Tier up</Button>
                  <Button size="xs" variant="subtle" disabled={index === tiers.length - 1} onClick={() => setTiers(move(tiers, index, index + 1))}>Tier down</Button>
                </Group>
              </Group>
              <Reasons reasons={reasonsFor(props.reasons, `tier ${tier.name}`)} />
            </Stack>
          </Card>
        );
      })}
    </Stack>
  );
}

// The harness profiles: each one's limits, resources and bash allowlist.
export function ProfilesForm(props: { document: Record<string, any>; onChange: (document: Record<string, any>) => void; reasons: string[] }) {
  const profiles: Record<string, any> = props.document.profiles ?? {};
  const setProfile = (name: string, profile: Record<string, any>) => props.onChange({ ...props.document, profiles: { ...profiles, [name]: profile } });
  return (
    <Stack gap="md">
      {Object.entries(profiles).map(([name, p]) => (
        <Card key={name} withBorder padding="md" data-testid={`profile-${name}`}>
          <Stack gap="sm">
            <Text fw={650}>{name}</Text>
            <Group grow>
              <NumberInput label="Max turns" min={1} value={p.max_turns} onChange={(v) => setProfile(name, { ...p, max_turns: Number(v) || 0 })} />
              <NumberInput label="Max minutes" min={1} value={p.max_minutes} onChange={(v) => setProfile(name, { ...p, max_minutes: Number(v) || 0 })} />
              <NumberInput label="CPU" min={0.5} step={0.5} decimalScale={1} value={p.resources?.cpu ?? ""} onChange={(v) => setProfile(name, { ...p, resources: { ...(p.resources ?? {}), cpu: Number(v) || 0 } })} />
              <NumberInput label="Memory, GB" min={1} value={p.resources?.memory_gb ?? ""} onChange={(v) => setProfile(name, { ...p, resources: { ...(p.resources ?? {}), memory_gb: Number(v) || 0 } })} />
            </Group>
            <TagsInput
              label="Bash allowlist"
              description="The programs the bash tool may run. Empty: the profile names no list."
              value={p.bash_allow ?? []}
              onChange={(v) => setProfile(name, v.length ? { ...p, bash_allow: v } : Object.fromEntries(Object.entries(p).filter(([k]) => k !== "bash_allow")))}
              clearable
            />
            <Reasons reasons={reasonsFor(props.reasons, `${name}:`)} />
          </Stack>
        </Card>
      ))}
    </Stack>
  );
}

// The lines of `after` that differ from `before`, each marked + or -, for the review before a save.
export function lineDiff(before: string, after: string): { sign: "+" | "-" | " "; text: string }[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const n = a.length;
  const m = b.length;
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  const out: { sign: "+" | "-" | " "; text: string }[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ sign: " ", text: a[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) out.push({ sign: "-", text: a[i++] });
    else out.push({ sign: "+", text: b[j++] });
  }
  while (i < n) out.push({ sign: "-", text: a[i++] });
  while (j < m) out.push({ sign: "+", text: b[j++] });
  return out;
}
