import { useEffect, useState, useSyncExternalStore } from "react";
import { Alert, Button, Group, Modal, Stack, Text, TextInput, Tooltip } from "@mantine/core";
import { IconAlertTriangle, IconHourglass, IconPlayerPlay, IconPlayerStop } from "@tabler/icons-react";
import { api, type KillSwitch } from "../api";
import { when } from "./ui";

// The kill switch on every page: a Stop everything button in the header for an admin, and while it is on, or a
// deploy drains, a banner over every page saying so, with who did it, when and why, and the way back.

// How often the console reads the switch again, so a change made elsewhere shows.
const REFRESH_MS = 15_000;

// One reading shared by the header button and the banner.
// readAt: when the state shown was read, so a reading the console could not refresh can say how old it is.
let current: { state: KillSwitch | null; error: string | null; readAt: number | null } = { state: null, error: null, readAt: null };
const listeners = new Set<() => void>();
function publish(next: typeof current) {
  current = next;
  listeners.forEach((listener) => listener());
}
// Reads the switch again now: after a change made on the Kill switch page.
export function read() {
  api.killSwitch().then(
    (state) => publish({ state, error: null, readAt: Date.now() }),
    (err: unknown) => publish({ ...current, error: err instanceof Error ? err.message : String(err) }),
  );
}
// Forgets the reading: between tests, which each start with their own switch.
export function forget() {
  current = { state: null, error: null, readAt: null };
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) read();
  const timer = window.setInterval(read, REFRESH_MS);
  return () => {
    listeners.delete(listener);
    window.clearInterval(timer);
  };
}
function useKillSwitch() {
  return useSyncExternalStore(subscribe, () => current);
}

type Change = "stop" | "lift" | "end-drain";

const ASK: Record<Change, { title: string; says: string; button: string; color: string; fallback: string }> = {
  stop: {
    title: "Stop everything?",
    says: "Every running task is stopped and no task starts until the kill switch is lifted. Pull requests already open stay open.",
    button: "Stop everything",
    color: "red",
    fallback: "stopped from the console",
  },
  lift: {
    title: "Lift the kill switch?",
    says: "Queued tasks start again, as the scheduler admits them. Tasks it stopped stay stopped; retry them from their pages.",
    button: "Lift the kill switch",
    color: "teal",
    fallback: "lifted",
  },
  "end-drain": {
    title: "End the drain?",
    says: "A deploy drains while it replaces the VM and ends the drain itself. End it by hand only if no deploy is running: the deploy runs are linked from Admin, Deploys.",
    button: "End the drain",
    color: "teal",
    fallback: "drain ended from the console",
  },
};

// Asks before changing the switch, with an optional reason that is recorded with the change.
function Confirm(props: { change: Change | null; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setReason("");
    setError(null);
  }, [props.change]);
  if (!props.change) return null;
  const ask = ASK[props.change];
  const change = props.change;
  async function go() {
    setBusy(true);
    try {
      const reasonGiven = reason.trim() || ask.fallback;
      const state =
        change === "stop" ? await api.setKillSwitch(true, reasonGiven)
        : change === "lift" ? await api.setKillSwitch(false, reasonGiven)
        : await api.setKillSwitchMode("off", reasonGiven);
      publish({ state, error: null, readAt: Date.now() });
      props.onClose();
    } catch (err) {
      setError(`${err instanceof Error ? err.message : String(err)}. Try again; if it keeps failing, Admin, Logs has the warning that says why.`);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal opened onClose={props.onClose} title={ask.title} centered>
      <Stack gap="md">
        <Text size="sm">{ask.says}</Text>
        <TextInput label="Reason" description="Recorded with the change, and shown to everyone while it lasts." value={reason} onChange={(e) => setReason(e.currentTarget.value)} data-autofocus />
        {error && <Alert color="red">{error}</Alert>}
        <Group justify="flex-end">
          <Button variant="default" onClick={props.onClose}>Cancel</Button>
          <Button color={ask.color} loading={busy} onClick={go}>{ask.button}</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

// The header button: Stop everything while the switch is off, Lift while it is on. Admins only.
export function KillSwitchButton() {
  const { state, error } = useKillSwitch();
  const [change, setChange] = useState<Change | null>(null);
  if (!state) {
    // The switch could not be read, which is when the services are unwell and a stop may be needed most: the
    // button is offered all the same. Before the first read answers, nothing shows.
    if (!error) return null;
    return (
      <>
        <Tooltip label="Could not read the kill switch. Stop every running task and start none">
          <Button size="xs" color="red" variant="light" leftSection={<IconPlayerStop size={14} />} onClick={() => setChange("stop")}>
            Stop everything
          </Button>
        </Tooltip>
        <Confirm change={change} onClose={() => setChange(null)} />
      </>
    );
  }
  // Stop everything is offered only while a task runs: with nothing running there is nothing to stop. An older
  // scheduler that does not say how many run keeps the button.
  if (!state.engaged && state.running === 0) return null;
  return (
    <>
      {state.engaged ? (
        <Button size="xs" color="teal" leftSection={<IconPlayerPlay size={14} />} onClick={() => setChange("lift")}>
          Lift kill switch
        </Button>
      ) : (
        <Tooltip label="Kill switch: stop every running task and start none">
          <Button size="xs" color="red" variant="light" leftSection={<IconPlayerStop size={14} />} onClick={() => setChange("stop")}>
            Stop everything
          </Button>
        </Tooltip>
      )}
      <Confirm change={change} onClose={() => setChange(null)} />
    </>
  );
}

// The banner over every page while the switch is on or a deploy drains. Nothing while it is off.
export function KillSwitchBanner({ admin }: { admin: boolean }) {
  const { state, error, readAt } = useKillSwitch();
  const [change, setChange] = useState<Change | null>(null);
  if (error && !state) {
    return (
      <Alert color="yellow" mb="md" title="Could not read the kill switch">
        {error}. It tries again in 15 seconds; reload to try now.
      </Alert>
    );
  }
  if (!state || (!state.engaged && !state.draining)) return null;
  const by = `${state.changed_by}, ${when(state.changed_at)}${state.reason ? `: ${state.reason}` : ""}`;
  return (
    <>
      <Alert
        mb="md"
        color={state.engaged ? "red" : "yellow"}
        icon={state.engaged ? <IconAlertTriangle /> : <IconHourglass />}
        title={state.engaged ? "Kill switch is on: nothing runs" : "Draining for a deploy: no new task starts, and running ones finish"}
        data-testid="kill-switch-banner"
      >
        <Group justify="space-between" wrap="wrap" gap="sm">
          <Stack gap={2}>
            <Text size="sm">{by}</Text>
            {error && (
              <Text size="xs" data-testid="kill-switch-stale">
                This could not be reread{readAt ? ` since ${when(new Date(readAt).toISOString())}` : ""}, so it may have changed: {error}. It tries again every 15 seconds.
              </Text>
            )}
          </Stack>
          {admin ? (
            <Button size="xs" color="teal" onClick={() => setChange(state.engaged ? "lift" : "end-drain")}>
              {state.engaged ? "Lift the kill switch" : "End the drain"}
            </Button>
          ) : (
            <Text size="sm">
              An admin can lift it.
            </Text>
          )}
        </Group>
      </Alert>
      <Confirm change={change} onClose={() => setChange(null)} />
    </>
  );
}
