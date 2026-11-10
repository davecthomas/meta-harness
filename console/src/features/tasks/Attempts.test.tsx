import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { CompareAttempts } from "./Attempts";

afterEach(() => vi.unstubAllGlobals());

test("attempts show side by side, and Pick this one sends the operator's choice", async () => {
  const posted: unknown[] = [];
  vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      posted.push(JSON.parse(String(init.body)));
      return Promise.resolve(new Response(JSON.stringify({ message_id: 1 }), { status: 202, headers: { "Content-Type": "application/json" } }));
    }
    return Promise.resolve(
      new Response(
        JSON.stringify({
          task_id: "t-1",
          waiting: true,
          attempts: [
            { attempt: 1, run_id: "t-1:run-1", candidate: "harness:pi-lean/claude:sonnet", decision: "finished", detail: "", spent_usd: "1.20", files: [{ path: "a.py", additions: 3, deletions: 1 }], patch: "+x" },
            { attempt: 2, run_id: "t-1:run-2", candidate: "harness:pi-lean/openai-responses:luna", decision: "final", detail: "max_turns", spent_usd: "2", files: [], patch: "" },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
  });
  render(
    <MantineProvider theme={theme}>
      <Notifications />
      <CompareAttempts taskId="t-1" />
    </MantineProvider>,
  );
  const first = await screen.findByTestId("attempt-1");
  expect(within(first).getByText("$1.20 · 1 files · +3 −1")).toBeTruthy();
  expect(within(screen.getByTestId("attempt-2")).queryByRole("button")).toBeNull();
  fireEvent.click(within(first).getByRole("button", { name: "Pick this one" }));
  await waitFor(() => expect(posted).toEqual([{ text: "1", when: "choose" }]));
});
