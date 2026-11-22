import { useState } from "react";
import { Anchor, Code, Loader, Modal, ScrollArea, Text } from "@mantine/core";
import { api } from "../../api";

// The specification a goal was planned from, on the goal's page: its name, and its text when asked for.
export function SpecView({ goalId, spec }: { goalId: string; spec: { name: string; chars: number } }) {
  const [opened, setOpened] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function open() {
    setOpened(true);
    if (text !== null) return;
    setError(null);
    api.goalSpec(goalId).then(
      (read) => setText(read.text),
      (err) => setError(`The specification could not be read: ${err instanceof Error ? err.message : String(err)}. Close this and open it again.`),
    );
  }

  return (
    <>
      <Text size="sm" mb="sm" data-testid="goal-spec">
        Planned from the specification{" "}
        <Anchor component="button" type="button" size="sm" onClick={open}>{spec.name}</Anchor>
        <Text span c="dimmed"> ({spec.chars.toLocaleString()} characters)</Text>
      </Text>
      <Modal opened={opened} onClose={() => setOpened(false)} title={spec.name} size="xl">
        {error ? <Text size="sm" c="red" role="alert">{error}</Text> : text === null ? <Loader size="sm" /> : (
          <ScrollArea.Autosize mah="70vh">
            <Code block data-testid="goal-spec-text">{text}</Code>
          </ScrollArea.Autosize>
        )}
      </Modal>
    </>
  );
}
