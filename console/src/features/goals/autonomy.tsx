import { SegmentedControl, Text, Tooltip } from "@mantine/core";

// How much Muxxy does for a goal's tasks without asking: three levels, named as a car's driving profiles are,
// since that is what they are. One place for their names, their one-line description and their hover help, so
// New goal, the goal's page and Settings say the same thing. The stored values stay ask, standard and full.

export type Autonomy = "ask" | "standard" | "full";

export const AUTONOMY: Record<Autonomy, { label: string; says: string; help: string }> = {
  ask: {
    label: "Chill",
    says: "It asks before it acts, and every pull request waits for you.",
    help: "Chill: the Task Manager asks you before it retries a task, sends one back or merges. You merge every pull request yourself. Slowest, and nothing happens that you did not agree to.",
  },
  standard: {
    label: "Standard",
    says: "It takes the clear steps itself, and a reviewed pull request waits for you to merge.",
    help: "Standard: it resolves conflicts, retries and fixes review findings by itself. Once a pull request passes its checks and review, it waits for you to merge it.",
  },
  full: {
    label: "Assertive",
    says: "It also merges each pull request once its checks and review pass.",
    help: "Assertive: everything Standard does, and it merges each pull request itself once its checks and review pass. Fastest: work reaches main with no one in the loop, inside the goal's budget.",
  },
};

export const autonomyOf = (goal: { autonomy?: Autonomy | null; auto_merge?: boolean }): Autonomy =>
  goal.autonomy ?? (goal.auto_merge ? "full" : "standard");

// The three levels to choose between: hovering a level says what it does, and the line under says what the
// chosen one does.
export function AutonomyPicker(props: { value: Autonomy; onChange: (value: Autonomy) => void; disabled?: boolean; size?: "xs" | "sm"; label: string }) {
  return (
    <div data-testid="autonomy">
      <SegmentedControl
        size={props.size ?? "sm"}
        aria-label={props.label}
        disabled={props.disabled}
        value={props.value}
        onChange={(value) => props.onChange(value as Autonomy)}
        data={(Object.keys(AUTONOMY) as Autonomy[]).map((value) => ({
          value,
          label: (
            <Tooltip label={AUTONOMY[value].help} multiline maw={320} openDelay={200}>
              <span data-testid={`autonomy-${value}`}>{AUTONOMY[value].label}</span>
            </Tooltip>
          ),
        }))}
      />
      <Text size="xs" c="dimmed" mt={4} data-testid="autonomy-says">{AUTONOMY[props.value].says}</Text>
    </div>
  );
}
