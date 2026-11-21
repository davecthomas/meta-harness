import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../theme";
import { VOICES, VoiceMark, VoicePanel } from "./Voice";

test("each speaker has its own name, color and mark", () => {
  render(<MantineProvider theme={theme}><VoiceMark who="planner" /><VoiceMark who="shepherd" /></MantineProvider>);

  expect(screen.getByRole("img", { name: "Goal Planner" })).toBeTruthy();
  expect(screen.getByRole("img", { name: "Task Shepherd" })).toBeTruthy();
  expect(VOICES.planner.color).not.toBe(VOICES.shepherd.color);
  const drawn = ["planner", "shepherd"].map((who) => screen.getByTestId(`voice-mark-${who}`).querySelector("svg")!.innerHTML);
  expect(drawn[0]).not.toBe(drawn[1]);
});

test("a speaker's panel carries its mark and its name over what it says", () => {
  render(<MantineProvider theme={theme}><VoicePanel who="shepherd" note="looked just now" testId="said">I have this one.</VoicePanel></MantineProvider>);

  const panel = screen.getByTestId("said");
  expect(panel.querySelector('[data-testid="voice-mark-shepherd"]')).toBeTruthy();
  expect(panel.textContent).toContain("Task Shepherd · looked just now");
  expect(panel.textContent).toContain("I have this one.");
});
