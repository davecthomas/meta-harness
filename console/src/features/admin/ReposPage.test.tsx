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

test("a repo's default checks edit in place, and each commit saves a new registry version", async () => {
  const saved: any[] = [];
  stub((body) => {
    saved.push(body);
    return respond({ ...REGISTRY, version: 4 });
  });
  fireEvent.click(await screen.findByRole("button", { name: "Edit Default acceptance checks of o/a" }));
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  const [input] = screen.getAllByLabelText("Default acceptance checks of o/a", { selector: "input" });
  fireEvent.change(input, { target: { value: "make lint" } });
  fireEvent.keyDown(input, { key: "Enter" });
  fireEvent.keyDown(input, { key: "Enter" });

  await waitFor(() => expect(saved).toHaveLength(1));
  expect(saved[0].document.repos["o/a"]).toEqual({ tracker: "github", default_checks: ["make test", "make lint"] });
  expect(saved[0].note).toBe("changed o/a");
});

test("a refused change to a repo's checks keeps the field open with the reason under it", async () => {
  stub(() => respond({ detail: { reasons: ["o/a: default_checks must be a list of commands"] } }, 422));
  fireEvent.click(await screen.findByRole("button", { name: "Edit Default acceptance checks of o/a" }));
  const [input] = screen.getAllByLabelText("Default acceptance checks of o/a", { selector: "input" });
  fireEvent.change(input, { target: { value: "make lint" } });
  fireEvent.keyDown(input, { key: "Enter" });
  fireEvent.keyDown(input, { key: "Enter" });

  expect(await screen.findByText("o/a: default_checks must be a list of commands")).toBeTruthy();
  expect(screen.getAllByLabelText("Default acceptance checks of o/a", { selector: "input" }).length).toBeGreaterThan(0);
});

test("removing a repo an open goal names shows the refusal", async () => {
  stub(() => respond({ detail: { reasons: ["o/a is named by open goal g-1 (Saves)"] } }, 422));
  fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
  expect(await screen.findByText("o/a is named by open goal g-1 (Saves)")).toBeTruthy();
});

test("two quick edits to different repos save one after the other, the second on the registry the first saved", async () => {
  let document: any = { repos: { "o/a": { tracker: "github", default_checks: ["make test"] }, "o/b": { tracker: "github", default_checks: [] } } };
  const saved: any[] = [];
  let release!: () => void;
  const first = new Promise<void>((resolve) => (release = resolve));
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url === "/v1/config/repos" && init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      saved.push(body);
      if (saved.length === 1) await first;
      document = body.document;
      return respond({ ...REGISTRY, version: 3 + saved.length, document });
    }
    if (url === "/v1/config/repos") return respond({ ...REGISTRY, document });
    if (url === "/v1/repos/available") return respond({ repos: [], install_urls: [] });
    return respond({ detail: "not stubbed" }, 404);
  });
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <ReposPage />
      </MemoryRouter>
    </MantineProvider>,
  );
  const commit = async (repo: string, check: string) => {
    fireEvent.click(await screen.findByRole("button", { name: `Edit Default acceptance checks of ${repo}` }));
    const [input] = screen.getAllByLabelText(`Default acceptance checks of ${repo}`, { selector: "input" });
    fireEvent.change(input, { target: { value: check } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.keyDown(input, { key: "Enter" });
  };

  await commit("o/a", "make lint");
  await waitFor(() => expect(saved).toHaveLength(1));
  await commit("o/b", "npm test");
  await new Promise((r) => setTimeout(r, 50));
  expect(saved).toHaveLength(1);
  release();

  await waitFor(() => expect(saved).toHaveLength(2));
  expect(saved[1].document.repos).toEqual({
    "o/a": { tracker: "github", default_checks: ["make test", "make lint"] },
    "o/b": { tracker: "github", default_checks: ["npm test"] },
  });
});
