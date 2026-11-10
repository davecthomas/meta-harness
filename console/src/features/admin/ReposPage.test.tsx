import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { ReposPage } from "./ReposPage";

afterEach(() => vi.unstubAllGlobals());

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const REGISTRY = {
  kind: "repos", version: 3, edited_by: "dave", note: null, origin: "edit", rolled_back_from: null, created_at: "2026-11-09T10:00:00Z",
  document: { repos: { "o/a": { tracker: "github", default_checks: ["make test"] } } },
};

function stub(put: (body: any) => Promise<Response>) {
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/config/repos" && init?.method === "PUT") return put(JSON.parse(String(init.body)));
    if (url === "/v1/config/repos") return respond(REGISTRY);
    if (url === "/v1/repos/available") return respond({ repos: [{ name: "o/b", private: false }], install_urls: ["https://github.com/apps/mh/installations/new"] });
    return respond({ detail: "not stubbed" }, 404);
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <ReposPage />
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("adding a repo saves the registry with it and its checks", async () => {
  const saved: any[] = [];
  stub((body) => {
    saved.push(body);
    return respond({ ...REGISTRY, version: 4 });
  });
  fireEvent.click(await screen.findByRole("button", { name: "Add repository" }));
  const dialog = await screen.findByRole("dialog", { name: "Add repository" });
  const [select] = within(dialog).getAllByLabelText(/Repository/, { selector: "input" });
  fireEvent.focus(select);
  fireEvent.change(select, { target: { value: "o/b" } });
  await screen.findByRole("option", { name: "o/b", hidden: true });
  fireEvent.keyDown(select, { key: "ArrowDown" });
  fireEvent.keyDown(select, { key: "Enter" });
  await waitFor(() => expect((select as HTMLInputElement).value).toBe("o/b"));
  fireEvent.change(within(dialog).getByLabelText(/Default acceptance checks/), { target: { value: "uv sync\nuv run pytest -q" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

  await waitFor(() => expect(saved).toHaveLength(1));
  expect(saved[0].document.repos["o/b"]).toEqual({ tracker: "github", default_checks: ["uv sync", "uv run pytest -q"] });
  expect(saved[0].note).toBe("added o/b");
});

test("removing a repo an open goal names shows the refusal", async () => {
  stub(() => respond({ detail: { reasons: ["o/a is named by open goal g-1 (Saves)"] } }, 422));
  fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
  expect(await screen.findByText("o/a is named by open goal g-1 (Saves)")).toBeTruthy();
});
