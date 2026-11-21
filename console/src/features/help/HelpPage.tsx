import { Alert, Anchor, Group, List, Stack, Text } from "@mantine/core";
import { IconInfoCircle } from "@tabler/icons-react";
import { Link, Navigate, useParams } from "react-router-dom";
import { PageHeader } from "../../components/ui";
import { TOPICS, topic, type Block } from "./topics";

// One Help topic: its text, and the topics before and after it. /help opens the first, Why.

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
  const { slug } = useParams();
  const shown = slug ? topic(slug) : TOPICS[0];
  // An address that names no topic, such as an old link, opens Help at its start.
  if (!shown) return <Navigate to="/help" replace />;
  const at = TOPICS.indexOf(shown);
  const before = TOPICS[at - 1];
  const after = TOPICS[at + 1];
  return (
    <Stack maw={720}>
      <PageHeader title={shown.title} />
      {shown.blocks.map((block, n) => <Shown key={n} block={block} />)}
      <Group justify="space-between" mt="md">
        {before ? <Anchor component={Link} to={`/help/${before.slug}`}>Back: {before.title}</Anchor> : <span />}
        {after && <Anchor component={Link} to={`/help/${after.slug}`}>Next: {after.title}</Anchor>}
      </Group>
    </Stack>
  );
}
