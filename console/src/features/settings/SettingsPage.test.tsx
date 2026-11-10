import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { SettingsPage } from "./SettingsPage";

afterEach(() => vi.unstubAllGlobals());

function respond(body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
}

const SAVED = {
  planner: { repos: [], modality: "fix", latitude: null, max_usd: null, auto_merge: false },
  appearance: "system",
  notifications: false,
};

test("a user changes their planner defaults and appearance and saves them", async () => {
  const put: any[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/me/settings" && init?.method === "PUT") {
      put.push(JSON.parse(String(init.body)));
      return respond(put.at(-1));
    }
    if (url === "/v1/me/settings") return respond(SAVED);
    if (url === "/v1/repos") return respond({ repos: [{ name: "o/a" }, { name: "o/b" }] });
    return respond({});
  });
  render(
    <MantineProvider theme={theme}>
      <Notifications />
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    </MantineProvider>,
  );

  fireEvent.click(await screen.findByRole("checkbox", { name: "o/b" }));
  fireEvent.click(screen.getByRole("switch", { name: "Merge pull requests without me" }));
  fireEvent.click(screen.getByText("Dark"));
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

  await waitFor(() => expect(put).toHaveLength(1));
  expect(put[0]).toMatchObject({ appearance: "dark", planner: { repos: ["o/b"], auto_merge: true } });
  expect(screen.getByText("Connected")).toBeTruthy();
});
