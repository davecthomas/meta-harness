import { cleanup, render, screen } from "@testing-library/react";
import { MantineProvider, Textarea } from "@mantine/core";
import { afterEach, expect, test } from "vitest";
import { theme } from "../theme";
import { AiTyping, aiWriting } from "./AiWriting";

afterEach(cleanup);

const field = (writing: boolean, value: string) =>
  render(<MantineProvider theme={theme}><Textarea label="Description" value={value} onChange={() => undefined} {...aiWriting(writing, value)} /></MantineProvider>);

test("a field a model is filling says so inside it, with a sparkle and three dots, and stays editable", () => {
  field(true, "");
  const marker = screen.getByTestId("ai-writing-field");
  expect(marker.className).toBe("mh-ai-in-field");
  expect(marker.textContent).toBe("AI is writing");
  expect(marker.querySelectorAll(".mh-ai-dots span")).toHaveLength(3);
  expect(screen.getByRole("status").textContent).toBe("AI is writing");
  expect((screen.getByLabelText("Description") as HTMLTextAreaElement).readOnly).toBe(false);
});

test("once text has arrived the marker moves to the field's corner, and it is gone when the writing ends", () => {
  field(true, "Half a description");
  expect(screen.getByTestId("ai-writing-field").className).toBe("mh-ai-in-corner");
  cleanup();
  field(false, "A whole description");
  expect(screen.queryByTestId("ai-writing-field")).toBeNull();
});

test("on its own line it names what is being written", () => {
  render(<MantineProvider theme={theme}><AiTyping>AI is reading docs/specs/a.md</AiTyping></MantineProvider>);
  expect(screen.getByTestId("ai-typing").textContent).toBe("AI is reading docs/specs/a.md");
});
