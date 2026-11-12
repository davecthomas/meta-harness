import { Alert, Anchor, List, Text } from "@mantine/core";
import { IconHandStop } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import type { GoalStanding as Standing } from "../../api";
import { when } from "../../components/ui";

// A goal that is going nowhere says so at the top of its page: an active goal with work left, none of it
// moving, waits on an operator, and this lists what for, longest waiting first. A goal that is moving, held
// (its status already says that) or done shows nothing here.

const sentence = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

// The goal's standing in a line, for a list: empty unless it waits on an operator.
export function standingNote(standing: Standing | null | undefined): string {
  return standing?.state === "needs_you" ? `Waits on you: ${standing.reason}` : "";
}

export function GoalStanding({ standing }: { standing: Standing | null | undefined }) {
  if (!standing || standing.state !== "needs_you") return null;
  return (
    <Alert color="orange" title="This goal waits on you" icon={<IconHandStop size={18} />} mb="md" data-testid="goal-standing">
      <Text size="sm">{sentence(standing.reason)}. {standing.ask}</Text>
      {standing.yours.length > 0 && (
        <List size="sm" spacing={4} mt="xs">
          {standing.yours.map((mine) => (
            <List.Item key={mine.task_id}>
              <Anchor component={Link} to={`/tasks/${mine.task_id}`} size="sm">{mine.title}</Anchor>
              <Text span size="sm">: {mine.ask || sentence(mine.reason)} </Text>
              <Text span size="xs" c="dimmed">(since {when(mine.since)})</Text>
            </List.Item>
          ))}
        </List>
      )}
    </Alert>
  );
}
