import { useState } from "react";
import { ActionIcon, Badge, Button, Code, Group, Stack, Table, Text, TextInput, Tooltip } from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { InlineField } from "../../components/InlineField";
import { DataTable } from "../../components/ui";
import { changed, Reasons, stageOptional, type Staging } from "./ConfigViews";

// The tools the harness server knows; a profile names some of them.
const TOOLS = ["read", "write", "edit", "ls", "find", "grep", "bash", "submit_review", "update_plan"];

// What a profile added from the table starts as: the lean profile's tools and limits, the default image.
const NEW_PROFILE = { image: null, tools: ["read", "write", "edit", "ls", "find", "grep", "bash"], max_turns: 80, max_minutes: 45 };

// The harness profiles, one row each: tools, limits, resources, bash allowlist and image, each edited in place.
export function ProfilesView({ staging: s }: { staging: Staging }) {
  const profiles: Record<string, any> = s.document.profiles ?? {};
  // Resources are both or neither: a value emptied drops it, and resources left empty are dropped.
  const stageResource = (name: string, key: string, value: number | null) => {
    const next = { ...(profiles[name].resources ?? {}) };
    if (value === null) delete next[key];
    else next[key] = value;
    s.stage(["profiles", name, "resources"], Object.keys(next).length ? next : undefined);
  };
  return (
    <DataTable head={["Profile", "Tools", "Limits", "Resources", "Bash allowlist", "Image", ""]} minWidth={980}>
      {Object.entries(profiles).map(([name, p]) => {
        const at = (...keys: string[]) => ["profiles", name, ...keys];
        return (
          <Table.Tr key={name} data-testid={`profile-${name}`}>
            <Table.Td>
              <Group gap={6} wrap="nowrap">
                <Text fw={600} size="sm">{name}</Text>
                {s.original.profiles?.[name] === undefined && <Badge size="xs" color="yellow" variant="light" tt="none">new</Badge>}
              </Group>
              <Reasons reasons={s.reasons(`profile:${name}`)} />
            </Table.Td>
            <Table.Td maw={260}>
              <InlineField kind="tags" label={`Tools of ${name}`} value={p.tools ?? []} data={TOOLS} changed={changed(s, at("tools"))} reasons={s.reasons(`profile:${name}:tools`)} readOnly={s.readOnly} onCommit={(v) => s.stage(at("tools"), v)} />
            </Table.Td>
            <Table.Td>
              <Stack gap={2}>
                <InlineField kind="number" label={`Max turns of ${name}`} value={p.max_turns ?? null} min={1} decimalScale={0} suffix=" turns" changed={changed(s, at("max_turns"))} reasons={s.reasons(`profile:${name}:max_turns`)} readOnly={s.readOnly} onCommit={(v) => s.stage(at("max_turns"), v)} />
                <InlineField kind="number" label={`Max minutes of ${name}`} value={p.max_minutes ?? null} min={1} suffix=" min" changed={changed(s, at("max_minutes"))} reasons={s.reasons(`profile:${name}:max_minutes`)} readOnly={s.readOnly} onCommit={(v) => s.stage(at("max_minutes"), v)} />
              </Stack>
            </Table.Td>
            <Table.Td>
              <Stack gap={2}>
                <InlineField kind="number" label={`vCPU of ${name}`} value={p.resources?.cpu ?? null} min={0.5} step={0.5} decimalScale={1} allowEmpty suffix=" vCPU" empty="default vCPU" changed={changed(s, at("resources", "cpu"))} reasons={s.reasons(`profile:${name}:resources.cpu`)} readOnly={s.readOnly} onCommit={(v) => stageResource(name, "cpu", v)} />
                <InlineField kind="number" label={`Memory of ${name}`} value={p.resources?.memory_gb ?? null} min={1} allowEmpty suffix=" GB" empty="default memory" changed={changed(s, at("resources", "memory_gb"))} reasons={s.reasons(`profile:${name}:resources.memory_gb`)} readOnly={s.readOnly} onCommit={(v) => stageResource(name, "memory_gb", v)} />
              </Stack>
            </Table.Td>
            <Table.Td maw={260}>
              <InlineField
                kind="tags"
                label={`Bash allowlist of ${name}`}
                value={p.bash_allow ?? []}
                monospace
                empty="any program"
                display={
                  p.bash_allow?.length ? (
                    <Tooltip multiline w={320} label={p.bash_allow.join(", ")}>
                      <Badge variant="outline" tt="none">{p.bash_allow.length} programs</Badge>
                    </Tooltip>
                  ) : undefined
                }
                changed={changed(s, at("bash_allow"))}
                reasons={s.reasons(`profile:${name}:bash_allow`)}
                readOnly={s.readOnly}
                onCommit={(v) => stageOptional(s, at("bash_allow"), v)}
              />
            </Table.Td>
            <Table.Td>
              <InlineField
                kind="text"
                label={`Image of ${name}`}
                value={p.image ?? ""}
                monospace
                display={<Code>{p.image ?? "default"}</Code>}
                changed={changed(s, at("image"))}
                reasons={s.reasons(`profile:${name}:image`)}
                readOnly={s.readOnly}
                onCommit={(v) => s.stage(at("image"), v || null)}
              />
            </Table.Td>
            <Table.Td>
              {!s.readOnly && (
                <ActionIcon variant="subtle" color="red" aria-label={`Remove profile ${name}`} onClick={() => s.stage(["profiles", name], undefined)}>
                  <IconTrash size={16} />
                </ActionIcon>
              )}
            </Table.Td>
          </Table.Tr>
        );
      })}
      {!s.readOnly && <AddProfileRow names={Object.keys(profiles)} onAdd={(name) => s.stage(["profiles", name], NEW_PROFILE)} />}
    </DataTable>
  );
}

function AddProfileRow({ names, onAdd }: { names: string[]; onAdd: (name: string) => void }) {
  const [name, setName] = useState("");
  const trimmed = name.trim();
  const why = !trimmed ? null : names.includes(trimmed) ? "A profile has that name" : /\s|\//.test(trimmed) ? "A name has no spaces or slashes" : null;
  const add = () => {
    if (!trimmed || why) return;
    onAdd(trimmed);
    setName("");
  };
  return (
    <Table.Tr>
      <Table.Td colSpan={7}>
        <Group gap="xs" align="flex-start">
          <TextInput
            size="xs"
            aria-label="New profile name"
            placeholder="New profile name"
            value={name}
            error={why}
            onChange={(e) => setName(e.currentTarget.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
          />
          <Button size="xs" variant="light" leftSection={<IconPlus size={14} />} disabled={!trimmed || why !== null} onClick={add}>
            Add profile
          </Button>
        </Group>
      </Table.Td>
    </Table.Tr>
  );
}
