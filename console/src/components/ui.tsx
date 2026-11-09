import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Alert, Anchor, Badge, Code, Group, Stack, Table, Text, Title } from "@mantine/core";
import { issueUrl } from "../api";
import { statusColor } from "../theme";

// Small pieces every page shares, so each page reads as layout and data.

export function PageHeader(props: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <Group justify="space-between" align="flex-start" mb="lg" wrap="wrap" gap="md">
      <Stack gap={4} style={{ minWidth: 0, flex: "1 1 320px" }}>
        <Title order={2} style={{ textWrap: "balance", overflowWrap: "anywhere" }}>{props.title}</Title>
        {props.description && <Text c="dimmed" size="sm">{props.description}</Text>}
      </Stack>
      {props.actions && <Group gap="sm">{props.actions}</Group>}
    </Group>
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
    <Badge color={statusColor(status)} variant="light" radius="sm" tt="none">
      {STATUS_LABELS[status] ?? status.replace(/_/g, " ")}
    </Badge>
  );
}

export function ErrorAlert({ error }: { error: string | null }) {
  return error ? <Alert color="red" mb="md" title="Something failed">{error}</Alert> : null;
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
