import { useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Button, Group, Modal, Text } from "@mantine/core";
import { IconDeviceFloppy, IconTrash } from "@tabler/icons-react";
import { UNSAFE_DataRouterContext, useBlocker } from "react-router-dom";

export interface UnsavedChangesOptions {
  // Whether anything differs from how the editor opened.
  dirty: boolean;
  // A line naming what is unsaved: "the new goal 'Fix the save bugs'".
  describe: string;
  // The editor's own save. Resolves true when saved, false when refused (the editor shows why).
  save: () => Promise<boolean>;
  // Drops the changes.
  discard: () => void;
}

export interface UnsavedChanges {
  // The modal and the route guard: render it once in the editor.
  element: ReactNode;
  // Runs a leave the router doesn't see (closing the editor, a tab inside it), after asking when dirty.
  confirm: (leave: () => void) => void;
  // Lets the editor leave without asking, after its own save: call it before navigating away.
  release: () => void;
}

// Leaving an editor with unsaved changes asks first: save, discard or keep editing. It covers links,
// the back button and URL changes (through the router), closing the tab or reloading (through the
// browser's own prompt), and any other leave the editor routes through confirm.
export function useUnsavedChanges(options: UnsavedChangesOptions): UnsavedChanges {
  const latest = useRef(options);
  latest.current = options;
  const released = useRef(false);
  const [pending, setPending] = useState<{ go: () => void; stay: () => void } | null>(null);
  const [saving, setSaving] = useState(false);

  // A new change after a release asks again.
  useEffect(() => {
    if (options.dirty) released.current = false;
  }, [options.dirty]);

  useEffect(() => {
    if (!options.dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      if (released.current) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [options.dirty]);

  const ask = useCallback((go: () => void, stay: () => void) => setPending({ go, stay }), []);
  const shouldBlock = useCallback(() => latest.current.dirty && !released.current, []);

  const confirm = useCallback(
    (leave: () => void) => {
      if (shouldBlock()) ask(leave, () => undefined);
      else leave();
    },
    [ask, shouldBlock],
  );

  async function save() {
    if (!pending) return;
    setSaving(true);
    try {
      if (await latest.current.save()) {
        released.current = true;
        const { go } = pending;
        setPending(null);
        go();
      }
    } finally {
      setSaving(false);
    }
  }

  function discard() {
    if (!pending) return;
    latest.current.discard();
    released.current = true;
    const { go } = pending;
    setPending(null);
    go();
  }

  function keepEditing() {
    pending?.stay();
    setPending(null);
  }

  const element = (
    <>
      <RouteGuard shouldBlock={shouldBlock} ask={ask} />
      <Modal opened={pending !== null} onClose={keepEditing} title="Save your changes?" centered>
        <Text size="sm" mb="lg">You have unsaved changes to {options.describe}.</Text>
        <Group justify="flex-end" gap="sm">
          <Button variant="default" onClick={keepEditing}>Keep editing</Button>
          <Button color="red" variant="light" leftSection={<IconTrash size={16} />} onClick={discard}>Discard</Button>
          <Button leftSection={<IconDeviceFloppy size={16} />} loading={saving} onClick={() => void save()}>Save</Button>
        </Group>
      </Modal>
    </>
  );

  return {
    element,
    confirm,
    release: () => {
      released.current = true;
    },
  };
}

// The router half: blocks in-app navigation while dirty. useBlocker needs a data router; under a plain
// router (as some tests render) only the browser prompt and confirm guard the editor.
function RouteGuard(props: { shouldBlock: () => boolean; ask: (go: () => void, stay: () => void) => void }) {
  const dataRouter = useContext(UNSAFE_DataRouterContext);
  return dataRouter ? <Blocker {...props} /> : null;
}

function Blocker(props: { shouldBlock: () => boolean; ask: (go: () => void, stay: () => void) => void }) {
  const blocker = useBlocker(({ currentLocation, nextLocation }) => {
    const moving = currentLocation.pathname !== nextLocation.pathname || currentLocation.search !== nextLocation.search;
    return moving && props.shouldBlock();
  });
  const { ask } = props;
  useEffect(() => {
    if (blocker.state === "blocked") ask(() => blocker.proceed(), () => blocker.reset());
  }, [blocker, ask]);
  return null;
}
