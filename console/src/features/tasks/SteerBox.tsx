import { useState } from "react";
import { Button, Group, Paper, Stack, Text, Textarea } from "@mantine/core";
import { IconPlayerStop, IconSend } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import { api } from "../../api";
import { ErrorAlert } from "../../components/ui";

// Steer a running task: a message the agent reads at its next turn, or one it gets when it would stop, or stop
// the run after the turn in progress, keeping its work. Each is recorded with the operator as actor.
export function SteerBox({ taskId }: { taskId: string }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function send(when: "next_turn" | "after_stop" | "stop") {
    setBusy(when);
    setError(null);
    try {
      await api.messageTask(taskId, when === "stop" ? "" : text.trim(), when);
      notifications.show({
        color: "teal",
        title: when === "stop" ? "Stopping after this turn" : "Message sent",
        message: when === "stop" ? "The run ends after its turn and keeps its work." : when === "next_turn" ? "The agent reads it at its next turn." : "The agent gets it when it would otherwise stop.",
      });
      if (when !== "stop") setText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Paper withBorder p="sm" mb="md">
      <Stack gap="xs">
        <Text size="sm" fw={600}>Steer the run</Text>
        <ErrorAlert error={error} />
        <Textarea aria-label="Message to the agent" placeholder="A message for the agent" autosize minRows={2} value={text} onChange={(e) => setText(e.currentTarget.value)} />
        <Group justify="space-between">
          <Group gap="xs">
            <Button size="xs" leftSection={<IconSend size={14} />} disabled={!text.trim()} loading={busy === "next_turn"} onClick={() => void send("next_turn")}>Send</Button>
            <Button size="xs" variant="default" disabled={!text.trim()} loading={busy === "after_stop"} onClick={() => void send("after_stop")}>Send after</Button>
          </Group>
          <Button size="xs" color="orange" variant="light" leftSection={<IconPlayerStop size={14} />} loading={busy === "stop"} onClick={() => void send("stop")}>
            Stop after this turn
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}
