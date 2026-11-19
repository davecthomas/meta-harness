import { useEffect, useState } from "react";
import { ActionIcon, Anchor, CopyButton, Tooltip } from "@mantine/core";
import { IconCheck, IconCopy } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { api, type Task } from "../api";

// A task is called by its short name ("Hint text contrast") wherever a person reads it; its id stays in the
// address and behind a copy button. A task the planner has not named yet is called by its title.

export const taskName = (task: Pick<Task, "title"> & { name?: string | null }): string => task.name || task.title;

// What each task is called, read once per page load: several places name the same task.
const names = new Map<string, Promise<{ name: string; title: string }>>();

function read(id: string): Promise<{ name: string; title: string }> {
  let found = names.get(id);
  if (!found) {
    found = api.task(id).then((t) => ({ name: taskName(t), title: t.title }));
    names.set(id, found);
    // A read that failed is tried again by whoever asks next.
    found.catch(() => names.delete(id));
  }
  return found;
}

// A link to a task, by its name, for a place that holds only the task's id.
export function TaskName({ id, size = "sm" }: { id: string; size?: "xs" | "sm" }) {
  const [task, setTask] = useState<{ name: string; title: string } | null>(null);
  useEffect(() => {
    let live = true;
    read(id).then((t) => live && setTask(t), () => undefined);
    return () => {
      live = false;
    };
  }, [id]);
  return <Anchor component={Link} to={`/tasks/${id}`} size={size} title={task?.title}>{task?.name ?? "Open the task"}</Anchor>;
}

// The id, for whoever needs it in a command or a log search: copied, not read.
export function CopyId({ id }: { id: string }) {
  return (
    <CopyButton value={id}>
      {({ copied, copy }) => (
        <Tooltip label={copied ? "Copied" : `Copy its ID, ${id}`} withArrow>
          <ActionIcon variant="subtle" color="gray" size="sm" onClick={copy} aria-label="Copy the task's ID">
            {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
          </ActionIcon>
        </Tooltip>
      )}
    </CopyButton>
  );
}
