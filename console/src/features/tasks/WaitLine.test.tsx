import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { Task } from "../../api";
import { theme } from "../../theme";
import { WaitLine } from "./WaitLine";

afterEach(() => vi.unstubAllGlobals());

const show = (task: Partial<Task>) =>
  render(<MantineProvider theme={theme}><MemoryRouter><WaitLine task={task as Task} /></MemoryRouter></MantineProvider>);
const hold = (kind: string, detail: Record<string, unknown> = {}) => ({ kind, reason: "r", detail, since: "", last_checked: "" });

test("a task waiting on the same files names the tasks changing them", async () => {
  const names: Record<string, string> = { "t-a": "Capture reviewer summaries", "t-b": "Hint text contrast" };
  vi.stubGlobal("fetch", (url: string) => {
    const id = String(url).split("/").pop()!;
    return Promise.resolve(new Response(JSON.stringify({ id, title: "t", name: names[id] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  show({ status: "queued", held: hold("repo_area", { holders: ["t-a", "t-b"] }) as Task["held"] });
  expect((await screen.findByRole("link", { name: "Capture reviewer summaries" })).getAttribute("href")).toBe("/tasks/t-a");
  expect(await screen.findByRole("link", { name: "Hint text contrast" })).toBeTruthy();
  expect(screen.getByTestId("wait-line").textContent).toMatch(/^Waits for Capture reviewer summaries and Hint text contrast, which are changing the same files$/);
});

test("other waits say why in a sentence, a blocked task says what it waits on, and a running task shows nothing", () => {
  const { unmount } = show({ status: "queued", held: hold("system_parallel") as Task["held"] });
  expect(screen.getByTestId("wait-line").textContent).toContain("The system is running as many tasks as it allows");
  unmount();
  const blocked = show({ status: "blocked", depends_on: ["jira:VL-1", "jira:VL-2"] });
  expect(screen.getByTestId("wait-line").textContent).toBe("Waits for 2 other issues to be done");
  blocked.unmount();
  const { container } = show({ status: "running", held: null });
  expect(container.querySelector('[data-testid="wait-line"]')).toBeNull();
});
