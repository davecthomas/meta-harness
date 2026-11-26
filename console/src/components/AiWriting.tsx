import type { ReactNode } from "react";
import { Group, Text } from "@mantine/core";
import { IconSparkles } from "@tabler/icons-react";

// What shows while a model writes something for the operator: a sparkle, the words, and three dots that rise
// and fall in turn, as a chat app shows someone typing. It says the field is being filled, so nobody needs to
// type there. Inside a field (aiWriting) or on its own line (AiTyping).

export function AiTyping({ children = "AI is writing", size = "sm" }: { children?: ReactNode; size?: "xs" | "sm" | "md" }) {
  return (
    <Group gap={6} wrap="nowrap" role="status" aria-live="polite" data-testid="ai-typing" className="mh-ai-typing">
      <IconSparkles size={size === "xs" ? 13 : 16} className="mh-ai-sparkle" aria-hidden />
      <Text size={size} c="grape" fw={500} span>{children}</Text>
      <span className="mh-ai-dots" aria-hidden>
        <span />
        <span />
        <span />
      </span>
    </Group>
  );
}

// Props for a TextInput or Textarea that a model is filling: while writing, the marker sits inside the field,
// over its empty space, or in its corner once text has arrived. The field stays editable: typing over it is
// the operator's choice, and the marker never takes a click.
export function aiWriting(writing: boolean, value: string, what = "AI is writing"): { inputContainer?: (field: ReactNode) => ReactNode } {
  if (!writing) return {};
  const empty = !value.trim();
  return {
    inputContainer: (field: ReactNode) => (
      <div style={{ position: "relative" }}>
        {field}
        <div className={empty ? "mh-ai-in-field" : "mh-ai-in-corner"} data-testid="ai-writing-field">
          <AiTyping size={empty ? "sm" : "xs"}>{what}</AiTyping>
        </div>
      </div>
    ),
  };
}
