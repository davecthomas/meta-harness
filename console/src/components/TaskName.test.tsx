import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../theme";
import { CopyId, TaskName, taskName } from "./TaskName";
import { TitleLink } from "./ui";

afterEach(() => vi.unstubAllGlobals());

const show = (node: React.ReactNode) => render(<MantineProvider theme={theme}><MemoryRouter>{node}</MemoryRouter></MantineProvider>);
const answer = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));

test("a task is called by its short name, and by its title until it has one", () => {
  expect(taskName({ title: "Help text is too dim to read", name: "Hint text contrast" })).toBe("Hint text contrast");
  expect(taskName({ title: "Help text is too dim to read", name: null })).toBe("Help text is too dim to read");
});

test("a place that holds only a task's id links it by name, with its title on hover", async () => {
  vi.stubGlobal("fetch", () => answer({ id: "t-name-1", title: "Help text is too dim to read", name: "Hint text contrast" }));
  show(<TaskName id="t-name-1" />);
  const link = await screen.findByRole("link", { name: "Hint text contrast" });
  expect(link.getAttribute("href")).toBe("/tasks/t-name-1");
  expect(link.getAttribute("title")).toBe("Help text is too dim to read");
});

test("a task that cannot be read is still linked, never shown as an id", async () => {
  vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify({ detail: "no such task" }), { status: 404, headers: { "Content-Type": "application/json" } })));
  const { container } = show(<TaskName id="t-name-2" />);
  expect(screen.getByRole("link", { name: "Open the task" }).getAttribute("href")).toBe("/tasks/t-name-2");
  expect(container.textContent).not.toContain("t-name-2");
});

test("a row shows a named task's name over its title, and an unnamed one's title over its id", () => {
  const named = show(<TitleLink to="/tasks/t-1" title="Help text is too dim to read" id="t-1" name="Hint text contrast" />);
  expect(screen.getByRole("link", { name: "Hint text contrast" })).toBeTruthy();
  expect(named.container.textContent).toContain("Help text is too dim to read");
  expect(named.container.textContent).not.toContain("t-1");
  named.unmount();
  const unnamed = show(<TitleLink to="/tasks/t-2" title="Router tiers pick from a list" id="t-2" />);
  expect(screen.getByRole("link", { name: "Router tiers pick from a list" })).toBeTruthy();
  expect(unnamed.container.textContent).toContain("t-2");
});

test("the id is behind a copy button, not printed", () => {
  const { container } = show(<CopyId id="t-abc" />);
  expect(screen.getByRole("button", { name: "Copy the task's ID" })).toBeTruthy();
  expect(container.textContent).not.toContain("t-abc");
});
