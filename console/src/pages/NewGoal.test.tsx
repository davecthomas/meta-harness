import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { NewGoal } from "./NewGoal";
import { theme } from "../theme";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

function fillAndPress(button: string, created: Record<string, unknown>[]) {
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/a" }] });
    if (url === "/v1/labels") return respond({ repos: ["o/a"], labels: [] });
    if (url === "/v1/goals" && init?.method === "POST") {
      created.push(JSON.parse(String(init.body)));
      return respond({ id: "g-1" }, 201);
    }
    return respond({ detail: "not stubbed" }, 404);
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/goals/new"]}>
        <Routes>
          <Route path="/goals/new" element={<NewGoal />} />
          <Route path="/goals/:goalId" element={<p>Goal page</p>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
  return (async () => {
    fireEvent.click(await screen.findByRole("checkbox", { name: "o/a" }));
    fireEvent.change(screen.getByLabelText(/Description/), { target: { value: "Fix the save bugs" } });
    fireEvent.change(screen.getByRole("combobox", { name: /^Label/ }), { target: { value: "bug" } });
    fireEvent.click(screen.getByRole("button", { name: button }));
    expect(await screen.findByText("Goal page")).toBeTruthy();
  })();
}

test("Save creates the goal without starting it and opens its page", async () => {
  const created: Record<string, unknown>[] = [];
  await fillAndPress("Save", created);
  await waitFor(() => expect(created).toHaveLength(1));
  expect(created[0]).toMatchObject({ statement: "Fix the save bugs", repos: ["o/a"], label: "bug", start: false });
});

test("Save and start creates the goal started", async () => {
  const created: Record<string, unknown>[] = [];
  await fillAndPress("Save and start", created);
  expect(created).toHaveLength(1);
  expect(created[0]).toMatchObject({ label: "bug", start: true });
});
