import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { SteerBox } from "./SteerBox";

afterEach(() => vi.unstubAllGlobals());

test("Send, Send after and Stop after this turn post the operator's word to the task", async () => {
  const posted: unknown[] = [];
  vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => {
    posted.push(JSON.parse(String(init?.body)));
    return Promise.resolve(new Response(JSON.stringify({ message_id: posted.length }), { status: 202, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <Notifications />
      <SteerBox taskId="t-1" />
    </MantineProvider>,
  );
  fireEvent.change(screen.getByLabelText("Message to the agent"), { target: { value: "Use the helper." } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(posted).toHaveLength(1));
  fireEvent.click(screen.getByRole("button", { name: "Stop after this turn" }));
  await waitFor(() => expect(posted).toEqual([{ text: "Use the helper.", when: "next_turn" }, { text: "", when: "stop" }]));
});
