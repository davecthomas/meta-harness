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

// One alert per cause: a task the failures alert above the board already explains (explained) is not listed here
// again, and a goal that waits only on its budget is the budget alert's to say (budgetShown). With nothing left
// of its own to say, this shows nothing.
// planning: the goal's plan is being written or waits to be decided. The planner's panel under this says where
// the plan stands, read as the page is, so this says nothing of the plan beside it.
export function GoalStanding({ standing, explained, budgetShown, planning }: { standing: Standing | null | undefined; explained?: Set<string>; budgetShown?: boolean; planning?: boolean }) {
  if (!standing || standing.state !== "needs_you") return null;
  const yours = standing.yours.filter((mine) => !explained?.has(mine.task_id));
  if (planning && yours.length === 0) return null;
  if (standing.yours.length > 0 && yours.length === 0) return null;
  if (standing.yours.length === 0 && budgetShown) return null;
  return (
    <Alert color="orange" title="This goal waits on you" icon={<IconHandStop size={18} />} mb="md" data-testid="goal-standing">
      <Text size="sm">{sentence(standing.reason)}. {standing.ask}</Text>
      {yours.length > 0 && (
        <List size="sm" spacing={4} mt="xs">
          {yours.map((mine) => (
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
