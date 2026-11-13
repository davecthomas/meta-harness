import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { IconUsers } from "@tabler/icons-react";
import { afterEach, expect, test, vi } from "vitest";
import { GoalHistory } from "../features/goals/GoalHistory";
import { TaskHistory } from "../features/tasks/TaskHistory";
import { AdminMenu } from "./AdminMenu";
import { theme } from "../theme";

// The left pane lists every goal and task, current and past, and folds the admin pages into one entry.

afterEach(() => vi.unstubAllGlobals());

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const goal = (id: string, title: string, status: string) => ({
  id, title, statement: title, status, started_at: "2026-11-10T00:00:00Z", repos: ["o/r"], label: null, modality: "fix",
});
const task = (id: string, title: string, status: string) => ({ id, title, status, goal_id: null });

function show(element: React.ReactElement, path = "/") {
  return render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={[path]}>{element}</MemoryRouter>
    </MantineProvider>,
  );
}

test("goals list current ones first and past ones folded away, and a goal opens to its tasks", async () => {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    if (url.startsWith("/v1/goals")) return respond({ goals: [goal("g-1", "Console UX", "active"), goal("g-2", "Old work", "met")], total: 2 });
    if (url.includes("goal_id=g-1")) return respond({ tasks: [task("t-1", "Fix the badge", "running")], total: 1 });
    return respond({ detail: "not stubbed" }, 404);
  });
  show(<GoalHistory />);

  expect((await screen.findByRole("link", { name: /Console UX/ })).getAttribute("href")).toBe("/goals/g-1");
  expect(screen.queryByRole("link", { name: /Old work/ })).toBeNull();
  expect(asked.some((u) => u.includes("goal_id"))).toBe(false);

  fireEvent.click(screen.getByRole("button", { name: "Open Console UX" }));
  expect((await screen.findByRole("link", { name: /Fix the badge/ })).getAttribute("href")).toBe("/tasks/t-1");
  expect(screen.getByRole("img", { name: "running" })).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Past goals (1)" }));
  expect(await screen.findByRole("link", { name: /Old work/ })).toBeTruthy();
});

test("the goal whose page is open starts open", async () => {
  vi.stubGlobal("fetch", (url: string) => {
    if (url.startsWith("/v1/goals")) return respond({ goals: [goal("g-1", "Console UX", "active")], total: 1 });
    if (url.includes("goal_id=g-1")) return respond({ tasks: [], total: 0 });
    return respond({ detail: "not stubbed" }, 404);
  });
  show(<GoalHistory />, "/goals/g-1");

  expect(await screen.findByText("No tasks yet.")).toBeTruthy();
});

test("tasks list the ones still going, and past ones a page at a time", async () => {
  vi.stubGlobal("fetch", (url: string) => {
    const offset = new URL(url, "http://x").searchParams;
    if (offset.get("status")?.startsWith("queued")) return respond({ tasks: [task("t-1", "Running one", "running")], total: 1 });
    const limit = Number(offset.get("limit"));
    const all = Array.from({ length: 60 }, (_, i) => task(`t-p${i}`, `Done ${i}`, "closed"));
    return respond({ tasks: all.slice(0, limit), total: all.length });
  });
  show(<TaskHistory />);

  expect(await screen.findByRole("link", { name: /Running one/ })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Past tasks" }));
  expect(await screen.findByRole("link", { name: /Done 49/ })).toBeTruthy();
  expect(screen.queryByRole("link", { name: /Done 50/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Show more (10 left)" }));
  expect(await screen.findByRole("link", { name: /Done 59/ })).toBeTruthy();
});

test("a list that could not be read says why and what to do", async () => {
  vi.stubGlobal("fetch", () => respond({ detail: "the planner did not answer" }, 503));
  show(<GoalHistory />);

  expect(await screen.findByText(/Could not read the goals: .*reload to try now/)).toBeTruthy();
});

test("the admin pages are one entry that opens a menu on a click, highlighted while one of them shows", async () => {
  const items = [{ label: "Users", to: "/admin/users", icon: IconUsers }];
  const { unmount } = show(<AdminMenu items={items} />, "/goals");
  expect(screen.queryByRole("menuitem", { name: "Users" })).toBeNull();
  expect(screen.getByRole("button", { name: "Admin" }).hasAttribute("data-active")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Admin" }));
  expect((await screen.findByRole("menuitem", { name: "Users" })).getAttribute("href")).toBe("/admin/users");
  unmount();

  show(<AdminMenu items={items} />, "/admin/users");
  expect(screen.getByRole("button", { name: "Admin" }).hasAttribute("data-active")).toBe(true);
});
