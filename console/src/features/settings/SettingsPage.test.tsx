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

test("each field saves when it is committed and says Saved, with no Save settings button", async () => {
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
  await waitFor(() => expect(put).toHaveLength(1));
  expect(put[0]).toMatchObject({ planner: { repos: ["o/b"], auto_merge: false } });
  expect(await screen.findByText("Saved")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Save settings" })).toBeNull();

  fireEvent.click(screen.getByRole("switch", { name: "Merge pull requests without me" }));
  await waitFor(() => expect(put).toHaveLength(2));
  expect(put[1]).toMatchObject({ planner: { repos: ["o/b"], auto_merge: true } });

  fireEvent.click(screen.getByRole("button", { name: "Edit Appearance" }));
  fireEvent.click(await screen.findByRole("option", { name: "Dark", hidden: true }));
  await waitFor(() => expect(put).toHaveLength(3));
  expect(put[2]).toMatchObject({ appearance: "dark", planner: { repos: ["o/b"], auto_merge: true } });

  fireEvent.click(screen.getByRole("button", { name: "Edit Budget" }));
  fireEvent.change(screen.getByLabelText("Budget"), { target: { value: "25" } });
  fireEvent.keyDown(screen.getByLabelText("Budget"), { key: "Enter" });
  await waitFor(() => expect(put).toHaveLength(4));
  expect(put[3].planner.max_usd).toBe("25");
  expect(screen.getByText("Connected")).toBeTruthy();
});
