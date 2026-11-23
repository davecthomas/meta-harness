import { useCallback, useEffect, useState } from "react";
import { Alert, Group, List, Stack, Text } from "@mantine/core";
import { ConfirmButton } from "../../components/ConfirmButton";
import { errorText, ReadFailed } from "../../components/ReadFailed";
import { api } from "../../api";
import { refLabel } from "../../api";

const SHOWN = 8;

// On an ended goal's page: the issues the goal created that are still open in its tracker, with the button
// that closes them as not planned. A goal canceled without closing them, or met with duplicates left over,
// otherwise keeps them open for nobody. Nothing shows when it left none.
export function LeftOpenIssues({ goalId, tracker, onClosed }: { goalId: string; tracker: string; onClosed: () => void }) {
  const [issues, setIssues] = useState<{ ref: string; title: string }[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [kept, setKept] = useState<string[]>([]);
  const load = useCallback(() => {
    api.leftOpen(goalId).then(
      (found) => {
        setIssues(found.issues);
        setFailed(null);
      },
      (err) => setFailed(errorText(err)),
    );
  }, [goalId]);
  useEffect(load, [load]);

  if (failed) return <ReadFailed what="the issues this goal left open" error={failed} />;
  if (!issues?.length) return null;
  const count = issues.length;
  const many = count !== 1;
  return (
    <Alert color="yellow" mb="md" title={`${count} ${many ? "issues" : "issue"} this goal created ${many ? "are" : "is"} still open in ${tracker}`} data-testid="left-open">
      <Stack gap="sm">
        <Text size="sm">The goal has ended, so nothing will work on {many ? "them" : "it"}. Close {many ? "them" : "it"} as not planned, or leave {many ? "them" : "it"} for another goal to take.</Text>
        <List size="sm" spacing={2}>
          {issues.slice(0, SHOWN).map((issue) => (
            <List.Item key={issue.ref}>
              {issue.title} <Text span size="xs" c="dimmed" ff="monospace">{refLabel(issue.ref)}</Text>
            </List.Item>
          ))}
        </List>
        {count > SHOWN && <Text size="xs" c="dimmed">and {count - SHOWN} more</Text>}
        {kept.length > 0 && (
          <Text size="sm" c="red" role="alert" data-testid="left-open-kept">
            {kept.length === 1 ? "One could not be closed" : `${kept.length} could not be closed`}: {kept.join("; ")}
          </Text>
        )}
        <Group>
          <ConfirmButton
            color="yellow"
            variant="filled"
            label={`Close ${many ? `all ${count}` : "it"} as not planned`}
            title={`Close ${count} ${many ? "issues" : "issue"} in ${tracker}?`}
            says={`Each is closed in ${tracker} as work that will not be done, with a note that its goal ended. An issue with a pull request still open is left as it is. You can reopen any of them in ${tracker}.`}
            confirm={`Close ${many ? "them" : "it"}`}
            cancel="Leave them open"
            onConfirm={async () => {
              const done = await api.closeLeftOpen(goalId);
              setKept(done.errors);
              load();
              onClosed();
            }}
          />
        </Group>
      </Stack>
    </Alert>
  );
}
