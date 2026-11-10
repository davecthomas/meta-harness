import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../theme";
import { CommandPalette } from "./CommandPalette";

afterEach(() => vi.unstubAllGlobals());

function renderShell() {
  vi.stubGlobal("fetch", () =>
    Promise.resolve(
      new Response(JSON.stringify({ goals: [{ id: "g-1", title: "Fix saves", status: "active" }], tasks: [{ id: "t-1", title: "Crash on save", status: "running", ref: "r" }] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/"]}>
        <CommandPalette admin={false} />
        <Routes>
          <Route path="/" element={<p>home</p>} />
          <Route path="/goals" element={<p>goals page</p>} />
          <Route path="/tasks/:id" element={<p>task page</p>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("Ctrl+K opens the palette, which finds a task and opens it", async () => {
  renderShell();
  fireEvent.keyDown(window, { key: "k", ctrlKey: true });
  fireEvent.change(await screen.findByLabelText("Search the console"), { target: { value: "save" } });
  fireEvent.click(await screen.findByText("Crash on save"));
  expect(await screen.findByText("task page")).toBeTruthy();
});

test("g then g goes to Goals, and ? lists the shortcuts", async () => {
  renderShell();
  fireEvent.keyDown(window, { key: "g" });
  fireEvent.keyDown(window, { key: "g" });
  expect(await screen.findByText("goals page")).toBeTruthy();
  fireEvent.keyDown(window, { key: "?" });
  expect(await screen.findByText("Open the command palette")).toBeTruthy();
});
