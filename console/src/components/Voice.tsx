import type { ReactNode } from "react";
import { Box, Group, Paper, Text } from "@mantine/core";

// Who is talking. Two parts of the meta-harness speak to a person, each in one place: the Goal Planner on a
// goal's page and the Task Shepherd on a task's. Each has a mark and a color of its own, shown in the corner
// of whatever it says, so a reader knows whose words they are before reading them.

export type Speaker = "planner" | "shepherd";

export const VOICES: Record<Speaker, { name: string; color: string }> = {
  planner: { name: "Goal Planner", color: "indigo" },
  shepherd: { name: "Task Shepherd", color: "teal" },
};

// The Goal Planner's mark: a flag at the end of a route, since it plans the way to a goal.
function PlannerGlyph() {
  return (
    <>
      <path d="M7 27 C10 19, 15 23, 17 15" strokeDasharray="1.5 3.5" />
      <circle cx="7" cy="27" r="1.6" fill="currentColor" stroke="none" />
      <path d="M17 15 V5" />
      <path d="M17 5 L27 8.5 L17 12 Z" fill="currentColor" />
    </>
  );
}

// The Task Shepherd's mark: a crook, since it keeps every task moving to its merge.
function ShepherdGlyph() {
  return (
    <path d="M12 29 V12.5 a6.5 6.5 0 0 1 13 0 c0 3.5 -4.5 3.5 -4.5 0.5" />
  );
}

// A speaker's mark, in its color: a round badge that follows the light and dark themes.
export function VoiceMark({ who, size = 34 }: { who: Speaker; size?: number }) {
  const { name, color } = VOICES[who];
  return (
    <Box
      component="span"
      role="img"
      aria-label={name}
      title={name}
      data-testid={`voice-mark-${who}`}
      style={{
        display: "inline-flex",
        flex: "none",
        width: size,
        height: size,
        borderRadius: "50%",
        background: `var(--mantine-color-${color}-light)`,
        color: `var(--mantine-color-${color}-light-color)`,
      }}
    >
      <svg viewBox="0 0 34 34" width={size} height={size} fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {who === "planner" ? <PlannerGlyph /> : <ShepherdGlyph />}
      </svg>
    </Box>
  );
}

// What a speaker says, in its own box: its mark in the top corner, its name over the words, and its color on
// the box's edge.
export function VoicePanel({ who, note, right, children, testId }: {
  who: Speaker;
  // A few words after the name, such as when it last looked.
  note?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  const { name, color } = VOICES[who];
  return (
    <Paper withBorder p="md" mb="md" radius="md" data-testid={testId} style={{ borderLeft: `5px solid var(--mantine-color-${color}-6)` }}>
      <Group gap="sm" wrap="nowrap" align="flex-start">
        <VoiceMark who={who} />
        <Box style={{ flex: 1, minWidth: 0 }}>
          <Group justify="space-between" wrap="nowrap" gap="sm" align="flex-start">
            <Text size="xs" tt="uppercase" fw={700} c={color} style={{ letterSpacing: 0.4 }}>
              {name}{note ? <> · {note}</> : null}
            </Text>
            {right}
          </Group>
          {children}
        </Box>
      </Group>
    </Paper>
  );
}
