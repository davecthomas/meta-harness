import { Badge, Group, Text } from "@mantine/core";
import { IconAlertTriangle, IconCheck } from "@tabler/icons-react";
import { Link, useInRouterContext } from "react-router-dom";
import type { RepoHealth } from "../../api";
import { baseWords, healthOf, useReposHealth } from "./health";

// How a repo's base stands, in a few words beside the repo wherever an operator picks or watches it. The mark
// differs in shape as well as color, and it leads to the Repos page. Nothing for a base not checked yet.
export function BaseBadge({ repo, link = true }: { repo: RepoHealth | undefined; link?: boolean }) {
  const words = baseWords(repo);
  // Drawn outside a router (a field on its own), it is the words alone.
  const routed = useInRouterContext();
  if (!repo?.base || !words) return null;
  const failing = repo.base.state === "failing";
  const fixing = failing && repo.fix && !repo.fix.asked && repo.fix.open;
  const badge = (
    <Badge
      data-testid="repo-base"
      color={failing ? "orange" : "green"}
      variant="light"
      tt="none"
      radius="sm"
      leftSection={failing ? <IconAlertTriangle size={12} aria-hidden /> : <IconCheck size={12} aria-hidden />}
      style={link && routed ? { cursor: "pointer" } : undefined}
    >
      {words}{fixing ? ", a fix is running" : ""}
    </Badge>
  );
  return link && routed ? <Link to="/repos" style={{ textDecoration: "none" }} onClick={(e) => e.stopPropagation()}>{badge}</Link> : badge;
}

// The marker for a repo known by name alone.
export function RepoMarker({ repo }: { repo: string }) {
  const { health } = useReposHealth();
  return <BaseBadge repo={healthOf(health, repo)} />;
}

// The sidebar's count of repos whose base fails. Nothing when none does.
export function FailingCount() {
  const { health } = useReposHealth();
  if (!health?.failing) return null;
  return (
    <Group gap={0}>
      <Badge size="sm" color="orange" variant="light" tt="none" data-testid="repos-failing">{health.failing} failing</Badge>
    </Group>
  );
}

// Under a repo picker: each chosen repo whose base fails, with its marker, so an operator knows before a goal
// starts there. Nothing when every chosen repo's base passes or is not checked yet.
export function FailingBases({ repos }: { repos: string[] }) {
  const { health } = useReposHealth();
  const failing = repos.map((name) => healthOf(health, name)).filter((r): r is RepoHealth => r?.base?.state === "failing");
  if (!failing.length) return null;
  return (
    <Group gap="xs" wrap="wrap" data-testid="failing-bases">
      {failing.map((r) => (
        <Group key={r.repo} gap={6} wrap="nowrap">
          <Text size="sm" ff="monospace">{r.repo}</Text>
          <BaseBadge repo={r} />
        </Group>
      ))}
      <Text size="sm" c="dimmed">The goal can start: its tasks are told what already fails.</Text>
    </Group>
  );
}
