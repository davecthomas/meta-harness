import { ActionIcon, Badge, Button, Card, Code, Group, SimpleGrid, Stack, Text, Tooltip } from "@mantine/core";
import { IconArrowDown, IconArrowUp, IconPlus, IconTrash } from "@tabler/icons-react";
import { InlineField } from "../../components/InlineField";
import { Facts } from "../../components/ui";
import { useState } from "react";
import { changed, Reasons, stageOptional, type Staging } from "./ConfigViews";
import { RemovedRows } from "./RemovedRows";
import { getIn, joinCandidate, move, setIn, splitCandidate } from "./staging";

const THINKING = ["off", "minimal", "low", "medium", "high", "max"];
const ON_EXHAUSTED = [
  { value: "escalate", label: "Escalate one tier up" },
  { value: "fail", label: "Fail" },
];

// With the value itself first when the list does not name it, so a select can show what the document holds.
function withValue(options: string[], value: string): string[] {
  return !value || options.includes(value) ? options : [value, ...options];
}

// The router: its classifier, default tier, what happens when a tier is used up, and each tier with its
// description, thinking level and candidates, which are a harness profile and a model each, in the order
// the run loop tries them.
export function TiersView({ staging: s, profiles, models }: { staging: Staging; profiles: string[]; models: string[] }) {
  const router = s.document;
  const tiers: any[] = router.tiers ?? [];
  const classifier: Record<string, any> = router.classifier ?? {};
  // The tiers removed here, each with the place it had. A rename also takes a name out of the document, so a
  // removal is remembered when it is made, not read back from the documents. One counts only while the active
  // version still has the tier and the staged document does not: a save or a discard ends it.
  const [gone, setGone] = useState<{ tier: any; index: number }[]>([]);
  const has = (list: any[] | undefined, name: string) => (list ?? []).some((t) => t.name === name);
  const removedTiers = gone
    .filter((g) => has(s.original.tiers, g.tier.name) && !has(tiers, g.tier.name))
    .map((g) => ({
      name: g.tier.name,
      restore: () => {
        const next = [...tiers];
        next.splice(Math.min(g.index, next.length), 0, g.tier);
        s.stage(["tiers"], next);
        setGone((all) => all.filter((x) => x !== g));
      },
    }));
  // A classifier left with no field is dropped, so the library's default applies.
  const stageClassifier = (key: string, value: unknown) => {
    const empty = value === null || value === undefined || value === "";
    const next = { ...classifier };
    if (empty) delete next[key];
    else next[key] = value;
    s.stage(["classifier"], Object.keys(next).length ? next : undefined);
  };
  const exhausted = router.on_exhausted ?? "fail";
  // A new tier goes last, the hardest work's place, named so no two share a name, with nothing to try yet:
  // the save is refused until it has a candidate, and says so on its card.
  const addTier = () => {
    const names = new Set(tiers.map((t) => t.name));
    let n = tiers.length + 1;
    while (names.has(`tier-${n}`)) n += 1;
    s.stage(["tiers"], [...tiers, { name: `tier-${n}`, description: "", thinking: "medium", models: [] }]);
  };
  return (
    <>
      <Text size="sm" c="dimmed" mb="md" maw={860}>
        A task is sent to a tier by how hard it looks, and tried on that tier's candidates in order: each is a harness
        profile (the agent's tools and limits) with a model. Tiers go from the easiest work to the hardest, which is
        the order "Escalate one tier up" follows, so their order matters.
      </Text>
      <Facts
        items={[
          [
            "Classifier",
            <Stack gap={4}>
              <Group gap="lg" wrap="wrap">
                <Group gap={6} wrap="nowrap">
                  <Text size="xs" c="dimmed">Engine</Text>
                  <InlineField kind="text" label="Classifier engine" value={classifier.engine ?? ""} empty="the library's default" changed={changed(s, ["classifier", "engine"])} readOnly={s.readOnly} onCommit={(v) => stageClassifier("engine", v)} />
                </Group>
                <Group gap={6} wrap="nowrap">
                  <Text size="xs" c="dimmed">Model</Text>
                  <InlineField kind="text" label="Classifier model" value={classifier.model ?? ""} empty="the engine's default" changed={changed(s, ["classifier", "model"])} readOnly={s.readOnly} onCommit={(v) => stageClassifier("model", v)} />
                </Group>
                <Group gap={6} wrap="nowrap">
                  <Text size="xs" c="dimmed">Confidence at least</Text>
                  <InlineField kind="number" label="Classifier minimum confidence" value={classifier.min_confidence ?? null} min={0} max={1} step={0.05} decimalScale={2} allowEmpty empty="any" changed={changed(s, ["classifier", "min_confidence"])} readOnly={s.readOnly} onCommit={(v) => stageClassifier("min_confidence", v)} />
                </Group>
              </Group>
              <Reasons reasons={s.reasons("classifier")} />
            </Stack>,
          ],
          [
            "Default tier",
            <InlineField
              kind="select"
              label="Default tier"
              value={router.default_tier ?? null}
              data={withValue(tiers.map((t) => t.name), router.default_tier ?? "")}
              empty="none"
              changed={changed(s, ["default_tier"])}
              reasons={s.reasons("default_tier")}
              readOnly={s.readOnly}
              onCommit={(v) => stageOptional(s, ["default_tier"], v)}
            />,
          ],
          [
            "When a tier is used up",
            <InlineField
              kind="select"
              label="When a tier is used up"
              value={exhausted}
              data={ON_EXHAUSTED.some((o) => o.value === exhausted) ? ON_EXHAUSTED : [{ value: exhausted, label: exhausted }, ...ON_EXHAUSTED]}
              changed={changed(s, ["on_exhausted"])}
              reasons={s.reasons("on_exhausted")}
              readOnly={s.readOnly}
              onCommit={(v) => s.stage(["on_exhausted"], v ?? undefined)}
            />,
          ],
        ]}
      />
      <SimpleGrid cols={{ base: 1, md: 2 }} mt="lg">
        {tiers.map((tier, index) => (
          <TierCard key={`${index}-${tier.name}`} staging={s} tier={tier} index={index} count={tiers.length} profiles={profiles} models={models} onRemoved={(removed, at) => setGone((all) => [...all.filter((g) => g.tier.name !== removed.name), { tier: removed, index: at }])} />
        ))}
      </SimpleGrid>
      {!s.readOnly && removedTiers.length > 0 && <div style={{ marginTop: 16 }}><RemovedRows what="tier" rows={removedTiers} /></div>}
      {tiers.length === 0 && (
        <Text size="sm" c="dimmed" mt="md" data-testid="no-tiers">
          No tier yet, so no task can be routed. Add one, give it a candidate, and make it the default tier.
        </Text>
      )}
      {!s.readOnly && (
        <Button mt="md" variant="default" leftSection={<IconPlus size={16} />} onClick={addTier}>Add a tier</Button>
      )}
    </>
  );
}

function TierCard({ staging: s, tier, index, count, profiles, models, onRemoved }: { staging: Staging; tier: any; index: number; count: number; profiles: string[]; models: string[]; onRemoved?: (tier: any, index: number) => void }) {
  const all: any[] = s.document.tiers ?? [];
  const isDefault = s.document.default_tier === tier.name;
  // Moving a tier changes which is easier and which harder: one staged change, the list in its new order.
  const moveTier = (to: number) => s.stage(["tiers"], move(all, index, to));
  const removeTier = () => {
    s.stage(["tiers"], all.filter((_, i) => i !== index));
    onRemoved?.(tier, index);
  };
  const candidates: string[] = tier.models ?? [];
  const path = ["tiers", index];
  const setCandidates = (next: string[]) => s.stage([...path, "models"], next);
  const original: string[] = getIn(s.original, [...path, "models"]) ?? [];
  // The candidates removed here, each with its place, to put back. One counts while the active version has
  // more of that candidate than the staged tier does.
  const [goneCandidates, setGoneCandidates] = useState<{ candidate: string; index: number }[]>([]);
  const times = (list: string[], candidate: string) => list.filter((c) => c === candidate).length;
  const removedCandidates = goneCandidates
    .filter((g, i, every) => every.slice(0, i + 1).filter((x) => x.candidate === g.candidate).length <= times(original, g.candidate) - times(candidates, g.candidate))
    .map((g) => ({
      name: g.candidate,
      restore: () => {
        const next = [...candidates];
        next.splice(Math.min(g.index, next.length), 0, g.candidate);
        setCandidates(next);
        setGoneCandidates((every) => every.filter((x) => x !== g));
      },
    }));
  const removeCandidate = (i: number) => {
    setGoneCandidates((every) => [...every, { candidate: String(candidates[i]), index: i }]);
    setCandidates(candidates.filter((_, j) => j !== i));
  };
  // Where this tier's refused-save reasons are targeted (see reasonTarget).
  const at = `tier:${tier.name}`;
  // A rename is one staged change: the tier's name, and the default tier when it named this one.
  const rename = (value: string) => {
    const name = value.trim();
    if (!name) return "A tier needs a name";
    if (name === tier.name) return null;
    const tiers: any[] = s.document.tiers ?? [];
    if (tiers.some((t, i) => i !== index && t.name === name)) return `Another tier is named ${name}`;
    let next = setIn(s.document, [...path, "name"], name);
    if (s.document.default_tier === tier.name) next = setIn(next, ["default_tier"], name);
    s.stage([], next);
    return null;
  };
  return (
    <Card withBorder padding="lg" data-testid={`tier-${tier.name}`}>
      <Group justify="space-between" mb="xs" wrap="nowrap">
        <Group gap="xs">
          <Badge variant="light" radius="sm">Tier {index + 1}</Badge>
          {isDefault && <Badge variant="light" color="indigo" radius="sm" tt="none">Default</Badge>}
          <InlineField
            kind="text"
            label={`Name of tier ${index + 1}`}
            value={tier.name ?? ""}
            display={<Text fw={650}>{tier.name}</Text>}
            changed={changed(s, [...path, "name"])}
            readOnly={s.readOnly}
            onCommit={rename}
          />
        </Group>
        <InlineField
          kind="select"
          label={`Thinking of ${tier.name}`}
          value={tier.thinking ?? null}
          data={withValue(THINKING, tier.thinking ?? "")}
          clearable
          display={tier.thinking ? <Badge variant="outline" tt="none">thinking {tier.thinking}</Badge> : undefined}
          empty="no thinking level"
          changed={changed(s, [...path, "thinking"])}
          reasons={s.reasons(`${at}:thinking`)}
          readOnly={s.readOnly}
          onCommit={(v) => stageOptional(s, [...path, "thinking"], v)}
        />
      </Group>
      {!s.readOnly && (
        <Group gap={4} mb="xs" data-testid={`tier-controls-${tier.name}`}>
          <Tooltip label="Easier work: move this tier earlier">
            <ActionIcon variant="subtle" color="gray" size="sm" disabled={index === 0} aria-label={`Move ${tier.name} earlier`} onClick={() => moveTier(index - 1)}>
              <IconArrowUp size={14} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Harder work: move this tier later">
            <ActionIcon variant="subtle" color="gray" size="sm" disabled={index === count - 1} aria-label={`Move ${tier.name} later`} onClick={() => moveTier(index + 1)}>
              <IconArrowDown size={14} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label={isDefault ? "This is the default tier: make another tier the default before removing it" : "Remove this tier"} multiline maw={260}>
            <ActionIcon variant="subtle" color="red" size="sm" disabled={isDefault} aria-label={`Remove ${tier.name}`} onClick={removeTier}>
              <IconTrash size={14} />
            </ActionIcon>
          </Tooltip>
          {getIn(s.original, ["tiers"])?.every((t: any) => t.name !== tier.name) && <Badge size="xs" color="yellow" variant="light" tt="none">not saved</Badge>}
        </Group>
      )}
      <InlineField
        kind="textarea"
        label={`Description of ${tier.name}`}
        value={tier.description ?? ""}
        empty="No description: the classifier reads it to place a task in this tier"
        display={tier.description ? <Text size="sm" c="dimmed" style={{ whiteSpace: "pre-wrap" }}>{tier.description}</Text> : undefined}
        changed={changed(s, [...path, "description"])}
        readOnly={s.readOnly}
        onCommit={(v) => stageOptional(s, [...path, "description"], v.trim())}
      />
      <Text size="xs" fw={600} c="dimmed" tt="uppercase" mt="md" mb={6}>Candidates, in order</Text>
      <Stack gap={6}>
        {candidates.map((candidate, i) => {
          const { profile, model } = splitCandidate(String(candidate));
          const was = original[i] === undefined ? null : splitCandidate(String(original[i]));
          const replace = (next: string) => setCandidates(candidates.map((m, j) => (j === i ? next : m)));
          const where = `candidate ${i + 1} in ${tier.name}`;
          return (
            <Stack key={i} gap={2}>
              <Group gap="xs" wrap="nowrap">
                <Text size="sm" c="dimmed" w={18}>{i + 1}.</Text>
                <InlineField
                  kind="select"
                  label={`Profile of ${where}`}
                  value={profile || null}
                  data={withValue(profiles, profile)}
                  display={profile ? <Badge variant="dot" tt="none">{profile}</Badge> : undefined}
                  empty="pick a profile"
                  changed={was?.profile !== profile}
                  reasons={s.reasons(`${at}:candidate:${i}:profile`)}
                  readOnly={s.readOnly}
                  onCommit={(v) => replace(joinCandidate(v ?? "", model))}
                />
                <InlineField
                  kind="select"
                  searchable
                  label={`Model of ${where}`}
                  value={model || null}
                  data={withValue(models, model)}
                  display={model ? <Code>{model}</Code> : undefined}
                  empty="pick a model"
                  changed={was?.model !== model}
                  reasons={s.reasons(`${at}:candidate:${i}:model`)}
                  readOnly={s.readOnly}
                  onCommit={(v) => replace(joinCandidate(profile, v ?? ""))}
                />
                {!s.readOnly && (
                  <Group gap={0} wrap="nowrap" ml="auto">
                    <ActionIcon variant="subtle" color="gray" aria-label={`Move ${where} up`} disabled={i === 0} onClick={() => setCandidates(move(candidates, i, i - 1))}><IconArrowUp size={16} /></ActionIcon>
                    <ActionIcon variant="subtle" color="gray" aria-label={`Move ${where} down`} disabled={i === candidates.length - 1} onClick={() => setCandidates(move(candidates, i, i + 1))}><IconArrowDown size={16} /></ActionIcon>
                    <ActionIcon variant="subtle" color="red" aria-label={`Remove ${where}`} onClick={() => removeCandidate(i)}><IconTrash size={16} /></ActionIcon>
                  </Group>
                )}
              </Group>
              <Reasons reasons={s.reasons(`${at}:candidate:${i}`)} />
            </Stack>
          );
        })}
        <Reasons reasons={s.reasons(`${at}:candidates`)} />
        {original.length > candidates.length && <Badge size="xs" color="yellow" variant="light" tt="none">not saved: {original.length - candidates.length} removed</Badge>}
        {!s.readOnly && <RemovedRows compact what={`candidate of ${tier.name}`} rows={removedCandidates} />}
      </Stack>
      {!s.readOnly && (
        <Group mt="sm">
          <Button size="xs" variant="light" leftSection={<IconPlus size={14} />} onClick={() => setCandidates([...candidates, joinCandidate(profiles[0] ?? "", "")])}>
            Add candidate to {tier.name}
          </Button>
        </Group>
      )}
      <Stack mt="sm" gap={4}>
        <Reasons reasons={s.reasons(at)} />
      </Stack>
    </Card>
  );
}
