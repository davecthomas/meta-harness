import { Anchor, Checkbox, Group, Loader, MultiSelect, Stack, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { ConnectButton } from "../github/GitHubConnect";

// Up to this many repos are offered as checkboxes; more are a searchable list, so a long one is not a wall.
export const CHECKBOXES_UP_TO = 8;

const mono = { fontFamily: "var(--mantine-font-family-monospace)" };

// The repos a goal may change, picked from those the operator may name (api.goalRepos): the registry's and the
// ones they can push to with their GitHub connection. A saved repo no longer offered stays, so it can be removed.
export function RepoPick({
  offered,
  value,
  onChange,
  label,
  description,
  required = false,
}: {
  offered: string[] | null;
  value: string[];
  onChange: (repos: string[]) => void;
  label?: string;
  description?: string;
  required?: boolean;
}) {
  const names = [...new Set([...(offered ?? []), ...value])];
  // Reading the repos can take a few seconds the first time: the field says so, where it will be.
  if (offered === null && names.length === 0) {
    return (
      <Stack gap={6} data-testid="repos-reading">
        <Text size="sm" fw={600}>{label ?? "Repositories"}</Text>
        <Group gap="xs"><Loader size="xs" /><Text size="sm" c="dimmed">Reading the repos you can reach.</Text></Group>
      </Stack>
    );
  }
  if (offered !== null && names.length === 0) {
    return (
      <Stack gap={6} data-testid="no-repos">
        <Text size="sm" fw={500}>{label ?? "Repositories"}</Text>
        <Text size="sm" c="orange">No repositories yet. Connect GitHub to work in any repo you can push to.</Text>
        <ConnectButton />
        <Text size="xs" c="dimmed">
          An admin can also list one under <Anchor component={Link} to="/config?kind=repos" size="xs">Configuration, Repositories</Anchor>.
        </Text>
      </Stack>
    );
  }
  if (names.length <= CHECKBOXES_UP_TO) {
    return (
      <Checkbox.Group label={label} aria-label={label ?? "Repos"} description={description} withAsterisk={required} value={value} onChange={onChange}>
        <Stack gap="xs" mt={label ? "xs" : 0}>
          {names.map((name) => <Checkbox key={name} value={name} label={name} styles={{ label: mono }} />)}
        </Stack>
      </Checkbox.Group>
    );
  }
  return (
    <MultiSelect
      label={label}
      aria-label={label ?? "Repos"}
      description={description}
      withAsterisk={required}
      data={names}
      value={value}
      onChange={onChange}
      searchable
      clearable
      // Enter picks the closest match, so a typed name is taken without reaching for the mouse.
      selectFirstOptionOnChange
      placeholder={value.length ? undefined : "Type to find a repo"}
      nothingFoundMessage="No repo you can push to has that name. Pick which GitHub accounts Muxxy uses in Settings."
      limit={50}
      styles={{ pill: mono, option: mono }}
    />
  );
}
