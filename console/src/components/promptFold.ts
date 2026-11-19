// Which runs' prompts the operator collapsed in a terminal. A prompt shows expanded until its
// run's prompt is collapsed, and stays collapsed in this browser after that: on the task page's
// Terminal, on the Tasks page's tiles, and after a reload. A prompt is known by the position of
// its `run_prompt` event, which each run records once, so the same run's prompt is the same key
// wherever it shows.
const KEY = "mh.collapsed-prompts";
// The newest collapsed prompts kept, so the stored list stays small.
const KEEP = 500;

function read(): number[] {
  try {
    const held = JSON.parse(window.localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(held) ? held.filter((n): n is number => typeof n === "number") : [];
  } catch {
    return [];
  }
}

export function collapsedPrompts(): Set<number> {
  return new Set(read());
}

export function setPromptCollapsed(position: number, collapsed: boolean): Set<number> {
  const held = read().filter((n) => n !== position);
  if (collapsed) held.push(position);
  const kept = held.slice(-KEEP);
  try {
    window.localStorage.setItem(KEY, JSON.stringify(kept));
  } catch {
    // Storage full or blocked: the choice holds for this page only.
  }
  return new Set(kept);
}
