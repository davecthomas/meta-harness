import { useState, type ReactNode } from "react";
import { Alert, Button, Group, Modal, Stack, Text, type ButtonProps } from "@mantine/core";
import { afterFailedChange } from "../client";

// A button that asks first. For an action that cannot be taken back, or that stops or ships work: the dialog
// says what will happen, in full, and the action runs only on its own button. While it runs the button shows
// it; if it fails the dialog stays open and says so: that nothing changed when the service refused it, and that
// it may have gone through when no answer came back.
export function ConfirmButton(props: ButtonProps & {
  // The button's words, and the dialog's own button when `confirm` is not given.
  label: string;
  // The dialog's title, as a question.
  title: string;
  // What will happen, and what will not.
  says: ReactNode;
  confirm?: string;
  // The dialog's way out, when "Not now" would read oddly beside the action's name.
  cancel?: string;
  // The action. A rejection is shown in the dialog.
  onConfirm: () => Promise<unknown> | unknown;
  "aria-label"?: string;
}) {
  const { label, title, says, confirm, cancel, onConfirm, ...button } = props;
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [after, setAfter] = useState("");
  async function go() {
    setBusy(true);
    setFailed(null);
    try {
      await onConfirm();
      setAsking(false);
    } catch (err) {
      setFailed(err instanceof Error ? err.message : String(err));
      setAfter(afterFailedChange(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Button {...button} onClick={() => { setFailed(null); setAsking(true); }}>{label}</Button>
      <Modal opened={asking} onClose={() => !busy && setAsking(false)} title={title} centered>
        <Stack gap="md">
          <Text size="sm" component="div">{says}</Text>
          {failed && (
            <Alert color="red" title={after === "Nothing changed." ? "That did not happen" : "No answer came back"} data-testid="confirm-failed">
              <Text size="sm">{failed}</Text>
              <Text size="sm" mt={4}>
                {after === "Nothing changed." ? "Nothing changed. Try again, or close this and fix what the message names." : after}
              </Text>
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" disabled={busy} onClick={() => setAsking(false)}>{cancel ?? "Not now"}</Button>
            <Button color={button.color} loading={busy} onClick={() => void go()}>{confirm ?? label}</Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
