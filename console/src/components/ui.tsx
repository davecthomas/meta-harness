import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Alert, Anchor, Badge, Button, Code, Group, Stack, Table, Text, Title } from "@mantine/core";
import { issueUrl } from "../api";
import { SIGNED_OUT } from "../client";
import { statusColor } from "../theme";

// Small pieces every page shares, so each page reads as layout and data.

// A page's title with its actions beside it, and its description below both, across the page's width.
export function PageHeader(props: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <Stack gap={6} mb="lg">
      <Group justify="space-between" align="flex-start" wrap="wrap" gap="md">
        <Title order={2} style={{ textWrap: "balance", overflowWrap: "anywhere", minWidth: 0, flex: "1 1 320px" }}>{props.title}</Title>
        {props.actions && <Group gap="sm">{props.actions}</Group>}
      </Group>
      {props.description && <Text c="dimmed" size="sm" component="div">{props.description}</Text>}
    </Stack>
  );
}

export function SectionTitle(props: { children: ReactNode; right?: ReactNode }) {
  return (
    <Group justify="space-between" mt="xl" mb="sm">
      <Title order={4}>{props.children}</Title>
      {props.right}
    </Group>
  );
}

// A status whose badge reads otherwise than its name.
const STATUS_LABELS: Record<string, string> = { not_started: "Not started" };

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge color={statusColor(status)} variant="light" radius="sm" tt="none" miw="max-content">
      {STATUS_LABELS[status] ?? status.replace(/_/g, " ")}
    </Badge>
  );
}

// A failure, with what to do about it. A page that knows the remedy passes it; otherwise the banner gives
// the general one: try again, and where to read why when it fails again. A lost session sends the operator
// to sign in again.
export function ErrorAlert({ error, remedy }: { error: string | null; remedy?: ReactNode }) {
  if (!error) return null;
  const signedOut = /not signed in|nobody is signed in/i.test(error);
  return (
    <Alert color="red" mb="md" title={signedOut ? "Your session ended" : "Something failed"} data-testid="error-alert">
      <Stack gap="xs">
        <Text size="sm">{signedOut ? "The console could not read this page because you are no longer signed in." : error}</Text>
        {remedy ?? (
          signedOut ? (
            <Group gap="xs">
              <Button size="compact-sm" variant="white" color="red" onClick={() => window.dispatchEvent(new Event(SIGNED_OUT))}>Sign in again</Button>
              <Text size="xs">You come back to this page after.</Text>
            </Group>
          ) : (
            <Group gap="xs">
              <Button size="compact-sm" variant="white" color="red" onClick={() => window.location.reload()}>Reload to try again</Button>
              <Text size="xs">
                If it fails again, the <Anchor component={Link} to={`/admin/logs?q=${encodeURIComponent(error.slice(0, 60))}&minutes=60`} size="xs">Logs</Anchor> page
                has the warning that names it (admins), or ask an admin to look there.
              </Text>
            </Group>
          )
        )}
      </Stack>
    </Alert>
  );
}

export function Id({ children }: { children: ReactNode }) {
  return <Text ff="monospace" size="xs" c="dimmed">{children}</Text>;
}

export function IssueLink({ issueRef }: { issueRef: string }) {
  const url = issueUrl(issueRef);
  return url ? <Anchor href={url} target="_blank" size="sm">{issueRef.replace(/^github:/, "")}</Anchor> : <Code>{issueRef}</Code>;
}

export function TitleLink(props: { to: string; title: string; id: string }) {
  return (
    <>
      <Anchor component={Link} to={props.to} fw={500} size="sm">{props.title}</Anchor>
      <Id>{props.id}</Id>
    </>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <Text c="dimmed" size="sm" py="md">{children}</Text>;
}

export function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// A table in a bordered, scrolling container, with the header row given as labels.
export function DataTable(props: { head: string[]; children: ReactNode; minWidth?: number }) {
  return (
    <Table.ScrollContainer minWidth={props.minWidth ?? 720} type="native">
      <Table withTableBorder>
        <Table.Thead>
          <Table.Tr>{props.head.map((h) => <Table.Th key={h}>{h}</Table.Th>)}</Table.Tr>
        </Table.Thead>
        <Table.Tbody>{props.children}</Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

// Label and value pairs, two columns wide on a large screen.
export function Facts({ items }: { items: [string, ReactNode][] }) {
  return (
    <Table variant="vertical" withTableBorder layout="fixed">
      <Table.Tbody>
        {items.map(([label, value]) => (
          <Table.Tr key={label}>
            <Table.Th w={180}>{label}</Table.Th>
            <Table.Td style={{ overflowWrap: "anywhere" }}>{value}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

// A pull request's link, named by its number, opening GitHub in a new tab. Every list that shows one uses it.
export function PrLink({ url, size = "sm" }: { url: string; size?: "xs" | "sm" }) {
  const number = url.replace(/\/$/, "").split("/").pop();
  return (
    <Anchor href={url} target="_blank" size={size} onClick={(e) => e.stopPropagation()}>
      #{number}
    </Anchor>
  );
}
