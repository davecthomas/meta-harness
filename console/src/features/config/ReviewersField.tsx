import { ActionIcon, Badge, Button, Code, Group, Stack, Text, Tooltip } from "@mantine/core";
import { IconArrowDown, IconArrowUp, IconPlus, IconTrash } from "@tabler/icons-react";
import { InlineField } from "../../components/InlineField";
import { Reasons, type Staging } from "./ConfigViews";
import { joinCandidate, move, splitCandidate } from "./staging";

// With the value itself first when the list does not name it, so a select can show what the document holds.
function withValue(options: string[], value: string): string[] {
  return !value || options.includes(value) ? options : [value, ...options];
}

// The task policy's reviewers, each a harness profile and a model chosen from what the configuration has, in
// the order the review run tries them. The same pair a tier's candidate is, and picked the same way: typed as
// harness:<profile>/<engine>:<model>, a wrong name was only found by a refused save.
export function ReviewersField({ staging: s, profiles, models }: { staging: Staging; profiles: string[]; models: string[] }) {
  const reviewers: string[] = s.document.review?.reviewers ?? [];
  const original: string[] = s.original.review?.reviewers ?? [];
  const set = (next: string[]) => s.stage(["review", "reviewers"], next);
  return (
    <Stack gap={6} data-testid="reviewers">
      {reviewers.map((reviewer, i) => {
        const { profile, model } = splitCandidate(String(reviewer));
        const was = original[i] === undefined ? null : splitCandidate(String(original[i]));
        const replace = (next: string) => set(reviewers.map((r, j) => (j === i ? next : r)));
        const where = `reviewer ${i + 1}`;
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
                <ActionIcon variant="subtle" color="gray" aria-label={`Move ${where} up`} disabled={i === 0} onClick={() => set(move(reviewers, i, i - 1))}><IconArrowUp size={16} /></ActionIcon>
                <ActionIcon variant="subtle" color="gray" aria-label={`Move ${where} down`} disabled={i === reviewers.length - 1} onClick={() => set(move(reviewers, i, i + 1))}><IconArrowDown size={16} /></ActionIcon>
                <Tooltip label={reviewers.length === 1 ? "A review needs a reviewer: add another before removing this one" : `Remove ${where}`} multiline maw={260}>
                  <ActionIcon variant="subtle" color="red" aria-label={`Remove ${where}`} disabled={reviewers.length === 1} onClick={() => set(reviewers.filter((_, j) => j !== i))}><IconTrash size={16} /></ActionIcon>
                </Tooltip>
              </Group>
            )}
          </Group>
        );
      })}
      {reviewers.length === 0 && <Text size="sm" c="dimmed">No reviewer: a save is refused until there is one.</Text>}
      <Text size="xs" c="dimmed">Tried in this order. One whose model wrote the pull request is skipped, so a different model always reviews.</Text>
      <Reasons reasons={s.reasons("reviewers")} />
      {original.length > reviewers.length && <Badge size="xs" color="yellow" variant="light" tt="none">not saved: {original.length - reviewers.length} removed</Badge>}
      {!s.readOnly && (
        <Group>
          <Button size="xs" variant="light" leftSection={<IconPlus size={14} />} onClick={() => set([...reviewers, joinCandidate(profiles[0] ?? "", "")])}>
            Add a reviewer
          </Button>
        </Group>
      )}
    </Stack>
  );
}
