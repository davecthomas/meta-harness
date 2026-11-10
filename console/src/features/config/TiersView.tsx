import { ActionIcon, Badge, Button, Card, Code, Group, SimpleGrid, Stack, Text } from "@mantine/core";
import { IconArrowDown, IconArrowUp, IconPlus, IconTrash } from "@tabler/icons-react";
import { InlineField } from "../../components/InlineField";
import { Facts } from "../../components/ui";
import { changed, Reasons, stageOptional, type Staging } from "./ConfigViews";
import { getIn, joinCandidate, move, splitCandidate } from "./staging";

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
  // A classifier left with no field is dropped, so the library's default applies.
  const stageClassifier = (key: string, value: unknown) => {
    const empty = value === null || value === undefined || value === "";
    const next = { ...classifier };
    if (empty) delete next[key];
    else next[key] = value;
    s.stage(["classifier"], Object.keys(next).length ? next : undefined);
  };
  const exhausted = router.on_exhausted ?? "fail";
  return (
    <>
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
          <TierCard key={`${index}-${tier.name}`} staging={s} tier={tier} index={index} profiles={profiles} models={models} />
        ))}
      </SimpleGrid>
    </>
  );
}

function TierCard({ staging: s, tier, index, profiles, models }: { staging: Staging; tier: any; index: number; profiles: string[]; models: string[] }) {
  const candidates: string[] = tier.models ?? [];
  const path = ["tiers", index];
  const setCandidates = (next: string[]) => s.stage([...path, "models"], next);
  const original: string[] = getIn(s.original, [...path, "models"]) ?? [];
  return (
    <Card withBorder padding="lg" data-testid={`tier-${tier.name}`}>
      <Group justify="space-between" mb="xs" wrap="nowrap">
        <Group gap="xs">
          <Badge variant="light" radius="sm">Tier {index + 1}</Badge>
          <Text fw={650}>{tier.name}</Text>
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
          readOnly={s.readOnly}
          onCommit={(v) => stageOptional(s, [...path, "thinking"], v)}
        />
      </Group>
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
            <Group key={i} gap="xs" wrap="nowrap">
              <Text size="sm" c="dimmed" w={18}>{i + 1}.</Text>
              <InlineField
                kind="select"
                label={`Profile of ${where}`}
                value={profile || null}
                data={withValue(profiles, profile)}
                display={profile ? <Badge variant="dot" tt="none">{profile}</Badge> : undefined}
                empty="pick a profile"
                changed={was?.profile !== profile}
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
                readOnly={s.readOnly}
                onCommit={(v) => replace(joinCandidate(profile, v ?? ""))}
              />
              {!s.readOnly && (
                <Group gap={0} wrap="nowrap" ml="auto">
                  <ActionIcon variant="subtle" color="gray" aria-label={`Move ${where} up`} disabled={i === 0} onClick={() => setCandidates(move(candidates, i, i - 1))}><IconArrowUp size={16} /></ActionIcon>
                  <ActionIcon variant="subtle" color="gray" aria-label={`Move ${where} down`} disabled={i === candidates.length - 1} onClick={() => setCandidates(move(candidates, i, i + 1))}><IconArrowDown size={16} /></ActionIcon>
                  <ActionIcon variant="subtle" color="red" aria-label={`Remove ${where}`} onClick={() => setCandidates(candidates.filter((_, j) => j !== i))}><IconTrash size={16} /></ActionIcon>
                </Group>
              )}
            </Group>
          );
        })}
        {original.length > candidates.length && <Badge size="xs" color="yellow" variant="light" tt="none">changed: {original.length - candidates.length} removed</Badge>}
      </Stack>
      {!s.readOnly && (
        <Group mt="sm">
          <Button size="xs" variant="light" leftSection={<IconPlus size={14} />} onClick={() => setCandidates([...candidates, joinCandidate(profiles[0] ?? "", "")])}>
            Add candidate to {tier.name}
          </Button>
        </Group>
      )}
      <Stack mt="sm" gap={4}>
        <Reasons reasons={s.reasons(`tier:${tier.name}`)} />
      </Stack>
    </Card>
  );
}
