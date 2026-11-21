import { useEffect } from "react";
import { Alert, Anchor, Group, List, Stack, Text, Title } from "@mantine/core";
import { IconInfoCircle } from "@tabler/icons-react";
import { Navigate, useLocation, useParams } from "react-router-dom";
import { PageHeader } from "../../components/ui";
import { TOPICS, type Block } from "./topics";

// Help is one page: every section in reading order, with a line of links to each at the top. A link to a
// section (/help#spend-limits) opens the page there.

function Shown({ block }: { block: Block }) {
  if (block.kind === "steps") {
    return (
      <List type="ordered" spacing="xs" size="md">
        {block.items.map((item) => <List.Item key={item}>{item}</List.Item>)}
      </List>
    );
  }
  if (block.kind === "terms") {
    return (
      <Stack gap="xs">
        {block.items.map(([term, says]) => (
          <Text key={term}><Text span fw={700}>{term}</Text> {says}</Text>
        ))}
      </Stack>
    );
  }
  if (block.kind === "note") {
    return <Alert color="gray" icon={<IconInfoCircle size={18} />}>{block.text}</Alert>;
  }
  return <Text>{block.text}</Text>;
}

export function HelpPage() {
  const { hash } = useLocation();
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView?.();
  }, [hash]);
  return (
    <Stack maw={720} gap="lg">
      <PageHeader title="Help" />
      <Group gap="md" data-testid="help-sections">
        {TOPICS.map((t) => <Anchor key={t.slug} href={`#${t.slug}`} size="sm">{t.title}</Anchor>)}
      </Group>
      {TOPICS.map((t) => (
        <Stack key={t.slug} gap="sm" component="section" id={t.slug} style={{ scrollMarginTop: 72 }}>
          <Title order={3}>{t.title}</Title>
          {t.blocks.map((block, n) => <Shown key={n} block={block} />)}
        </Stack>
      ))}
    </Stack>
  );
}

// An address from when each section had a page of its own opens the one page at that section.
export function OldTopic() {
  const { slug } = useParams();
  return <Navigate to={TOPICS.some((t) => t.slug === slug) ? `/help#${slug}` : "/help"} replace />;
}
