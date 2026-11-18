import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { Task } from "../../api";
import { theme } from "../../theme";
import { Remedies } from "./Remedies";
import type { Diagnosis } from "./diagnosis";

afterEach(() => vi.unstubAllGlobals());

const SPEND_LIMIT: Diagnosis = {
  kind: "spend_limit",
  summary: "The task used up its $10.00 spend limit",
  cause: "Its runs together reached the task's spend limit before the work was finished.",
  fix: "Retry with a higher spend limit.",
  remedies: ["retry_more", "task"],
  detail: "spend_limit",
};

const TASKS = [
  { id: "t-1", max_usd: "10.00" },
  { id: "t-2", max_usd: "20.00" },
] as Pick<Task, "id" | "max_usd">[];

function show(tasks: Pick<Task, "id" | "max_usd">[], diagnosis: Diagnosis = SPEND_LIMIT) {
  const asked: { url: string; body: any }[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    asked.push({ url, body: JSON.parse(String(init?.body ?? "{}")) });
    return Promise.resolve(new Response(JSON.stringify({ id: "t-1", status: "queued" }), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <Remedies diagnosis={diagnosis} tasks={tasks} />
      </MemoryRouter>
    </MantineProvider>,
  );
  return { asked };
}

test("several tasks that used up their spend limit ask first, and the total is the sum of their doubled limits", async () => {
  const { asked } = show(TASKS);

  fireEvent.click(screen.getByRole("button", { name: "Retry all 2" }));
  const dialog = within(await screen.findByRole("dialog"));
  expect(dialog.getByText("Retry 2 tasks with spend limits that add up to $60.00, double what they had.")).toBeTruthy();
  expect(asked).toHaveLength(0);

  fireEvent.click(dialog.getByRole("button", { name: "Retry all 2" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(asked).toHaveLength(2);
  expect(asked[0].body).toEqual({ max_usd: 20 });
  expect(asked[1].body).toEqual({ max_usd: 40 });
});

test("a single task, and a retry that does not raise limits, retry without asking", async () => {
  const { asked: single } = show([TASKS[0]]);
  fireEvent.click(screen.getByRole("button", { name: /Retry with \$20\.00/ }));
  await waitFor(() => expect(single).toHaveLength(1));
  expect(screen.queryByRole("dialog")).toBeNull();

  const NO_RAISE: Diagnosis = { ...SPEND_LIMIT, kind: "limit", remedies: ["retry", "task"] };
  const { asked: many } = show(TASKS, NO_RAISE);
  fireEvent.click(screen.getByRole("button", { name: "Retry all 2" }));
  await waitFor(() => expect(many).toHaveLength(2));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(many[0].body).toEqual({ max_usd: 10 });
  expect(many[1].body).toEqual({ max_usd: 20 });
});
