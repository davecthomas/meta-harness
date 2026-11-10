// How a goal's parallel limit reads: its own, or the system's it inherits.
export function parallelLabel(own: number | null | undefined, system: number | null): string {
  if (own) return `${own}, set for this goal`;
  return system ? `${system}, the system setting` : "The system setting";
}

export const PARALLEL_HELP = "Leave empty to use the system setting. A lower number holds this goal to fewer tasks at once.";
