import { useEffect, useRef, useState } from "react";
import { ActionIcon, Badge, Button, Group, Stack, Table, Text, TextInput, Tooltip } from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { api } from "../../api";
import { InlineField } from "../../components/InlineField";
import { PickedName, Unreadable } from "../../components/PickedName";
import { DataTable } from "../../components/ui";
import { changed, Reasons, stageOptional, type Staging } from "./ConfigViews";
import { RemovedRows, removedEntries } from "./RemovedRows";

// The tools the harness server knows; a profile names some of them.
const TOOLS = ["read", "write", "edit", "ls", "find", "grep", "bash", "submit_review", "update_plan"];

// What a profile added from the table starts as: the lean profile's tools and limits, the default image.
const NEW_PROFILE = { image: null, tools: ["read", "write", "edit", "ls", "find", "grep", "bash"], max_turns: 80, max_minutes: 45 };

// The harness images the deployment builds, which a profile's image is picked from: known is null while they
// load, and error says why they could not be listed. fallback: the image a profile with no image runs.
function useImages() {
  const [known, setKnown] = useState<string[] | null>(null);
  const [fallback, setFallback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let stale = false;
    api.configImages().then(
      (r) => {
        if (stale) return;
        setKnown(r.images.map((i) => i.name));
        setFallback(r.default);
      },
      (err) => !stale && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      stale = true;
    };
  }, []);
  return { known, fallback, error };
}

// The programs one harness image has: known is null while they load, and error says why they could not be
// listed. image: the name they were listed under, the default image's own name when the profile names none.
type ProgramList = { image: string | null; known: string[] | null; error: string | null };

// The programs of each image the profiles run, by the profile's image ("" for the default), each read once.
function usePrograms(wanted: string[]) {
  const [lists, setLists] = useState<Record<string, ProgramList>>({});
  const asked = useRef(new Set<string>());
  const key = JSON.stringify(Array.from(new Set(wanted)).sort());
  useEffect(() => {
    for (const image of JSON.parse(key) as string[]) {
      if (asked.current.has(image)) continue;
      asked.current.add(image);
      const set = (list: ProgramList) => setLists((all) => ({ ...all, [image]: list }));
      api.configImagePrograms(image || undefined).then(
        (r) => set({ image: r.image, known: r.programs, error: null }),
        (err) => set({ image: image || null, known: null, error: err instanceof Error ? err.message : String(err) }),
      );
    }
  }, [key]);
  return (image: string): ProgramList => lists[image] ?? { image: image || null, known: null, error: null };
}

// The list's own spelling of a program: the exact name first, else one that differs only in capitals.
function onList(known: string[], name: string): string | undefined {
  return known.find((k) => k === name) ?? known.find((k) => k.toLowerCase() === name.toLowerCase());
}

// A profile's bash allowlist, picked from the programs its image has, since a name the image lacks allows
// nothing. A name the list lacks is refused and a saved one is marked. While the list loads, or when it cannot
// be read (the field says why), names are typed.
function AllowlistField({ s, name, value, list }: { s: Staging; name: string; value: string[]; list: ProgramList }) {
  const at = ["profiles", name, "bash_allow"];
  const known = list.known?.length ? list.known : null;
  const where = list.image ? `The harness image ${list.image}` : "The default harness image";
  const missing = known ? value.filter((p) => !onList(known, p)) : [];
  const commit = (next: string[]) => {
    if (!known) return stageOptional(s, at, next);
    const lacking = next.filter((p) => !onList(known, p));
    if (lacking.length) return `${where} has no program named ${lacking.join(", ")}: pick each from the list`;
    stageOptional(s, at, Array.from(new Set(next.map((p) => onList(known, p)!))));
  };
  return (
    <Stack gap={2}>
      <InlineField
        kind="tags"
        label={`Bash allowlist of ${name}`}
        value={value}
        data={known ?? []}
        monospace
        empty="any program"
        placeholder={known ? "Pick a program, Enter to add; Enter again to save" : list.error ? "Type each program's exact name" : undefined}
        display={
          value.length ? (
            <Tooltip multiline w={320} label={value.join(", ")}>
              <Badge variant="outline" tt="none">{value.length} programs</Badge>
            </Tooltip>
          ) : undefined
        }
        changed={changed(s, at)}
        reasons={s.reasons(`profile:${name}:bash_allow`)}
        readOnly={s.readOnly}
        onCommit={commit}
      />
      {missing.length > 0 && (
        <Text size="xs" c="orange" data-testid={`bash-unknown-${name}`}>{where} has no program named {missing.join(", ")}</Text>
      )}
      <Unreadable what={list.image ? `programs of ${list.image}` : "programs of the default image"} error={list.error} />
    </Stack>
  );
}

// The harness profiles, one row each: tools, limits, resources, bash allowlist and image, each edited in place.
// skills: every skill a profile may name, from the skills configuration. A profile's image is picked from the
// images the deployment builds, since a run on an image that does not exist fails at start; empty is the default.
export function ProfilesView({ staging: s, skills = [] }: { staging: Staging; skills?: string[] }) {
  const profiles: Record<string, any> = s.document.profiles ?? {};
  const images = useImages();
  const programs = usePrograms(Object.values(profiles).map((p) => p?.image ?? ""));
  // Resources are both or neither: a value emptied drops it, and resources left empty are dropped.
  const stageResource = (name: string, key: string, value: number | null) => {
    const next = { ...(profiles[name].resources ?? {}) };
    if (value === null) delete next[key];
    else next[key] = value;
    s.stage(["profiles", name, "resources"], Object.keys(next).length ? next : undefined);
  };
  return (
    <>
    <DataTable head={["Profile", "Tools", "Skills", "Limits", "Resources", "Bash allowlist", "Image", ""]} minWidth={1120}>
      {Object.entries(profiles).map(([name, p]) => {
        const at = (...keys: string[]) => ["profiles", name, ...keys];
        return (
          <Table.Tr key={name} data-testid={`profile-${name}`}>
            <Table.Td>
              <Group gap={6} wrap="nowrap">
                <Text fw={600} size="sm">{name}</Text>
                {s.original.profiles?.[name] === undefined && <Badge size="xs" color="yellow" variant="light" tt="none">not saved</Badge>}
              </Group>
              <Reasons reasons={s.reasons(`profile:${name}`)} />
            </Table.Td>
            <Table.Td maw={260}>
              <InlineField kind="tags" label={`Tools of ${name}`} value={p.tools ?? []} data={TOOLS} changed={changed(s, at("tools"))} reasons={s.reasons(`profile:${name}:tools`)} readOnly={s.readOnly} onCommit={(v) => s.stage(at("tools"), v)} />
            </Table.Td>
            <Table.Td maw={220}>
              <InlineField
                kind="tags"
                label={`Skills of ${name}`}
                value={p.skills ?? []}
                data={skills}
                empty="none"
                changed={changed(s, at("skills"))}
                reasons={s.reasons(`profile:${name}:skills`)}
                readOnly={s.readOnly}
                onCommit={(v) => stageOptional(s, at("skills"), v)}
              />
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
              <AllowlistField s={s} name={name} value={p.bash_allow ?? []} list={programs(p.image ?? "")} />
            </Table.Td>
            <Table.Td>
              <PickedName
                label={`Image of ${name}`}
                what="harness image"
                where="The deployment"
                value={p.image ?? ""}
                empty={images.fallback ? `default (${images.fallback})` : "default"}
                known={images.known}
                unreadable={images.error}
                testId={`image-unknown-${name}`}
                changed={changed(s, at("image"))}
                readOnly={s.readOnly}
                onCommit={(v) => s.stage(at("image"), v || null)}
              />
              <Reasons reasons={s.reasons(`profile:${name}:image`)} />
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
    <Unreadable what="harness images" error={images.error} />
    {!s.readOnly && <RemovedRows what="profile" rows={removedEntries(s, "profiles")} />}
    </>
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
