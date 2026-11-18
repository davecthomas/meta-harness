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
  // How it ended, who ran it and what it changed read in words, not as the service names them.
  expect(within(first).getByText("Finished")).toBeTruthy();
  expect(within(first).getByTestId("candidate-name").textContent).toBe("sonnet, profile pi-lean");
  expect(within(first).getByText("$1.20 spent · 1 file changed · 3 lines added, 1 removed")).toBeTruthy();
  const second = screen.getByTestId("attempt-2");
  expect(within(second).getByText("Did not finish, the last try")).toBeTruthy();
  expect(within(second).getByTestId("attempt-why").textContent).toContain("Recorded: max_turns");
  expect(within(screen.getByTestId("attempt-2")).queryByRole("button")).toBeNull();
  fireEvent.click(within(first).getByRole("button", { name: "Pick this one" }));
  await waitFor(() => expect(posted).toEqual([{ text: "1", when: "choose" }]));
});
