import { fireEvent, render, screen, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { ReposView } from "./ReposView";
import { theme } from "../../theme";

// Configuration, Repositories is the one place a repository is added, removed and edited. Each change stages.

afterEach(() => vi.unstubAllGlobals());

function show(repos: Record<string, unknown>, original = repos) {
  const staged: [string[], unknown][] = [];
  vi.stubGlobal("fetch", (url: string) =>
    Promise.resolve(
      new Response(
        JSON.stringify(
          url === "/v1/repos/available"
            ? { repos: [{ name: "o/a", private: false }, { name: "o/b", private: true }], install_urls: ["https://github.com/apps/x/installations/new"] }
            : {},
        ),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
  render(
    <MantineProvider theme={theme}>
      <ReposView staging={{ document: { repos }, original: { repos: original }, stage: (path, value) => staged.push([path as string[], value]), reasons: () => [] }} />
    </MantineProvider>,
  );
  return staged;
}

test("with no repository it says nothing can run yet, and one is added here from the App's repositories", async () => {
  const staged = show({});

  expect(within(screen.getByTestId("no-repos")).getByText(/nothing can run until there is one/)).toBeTruthy();
  expect(await screen.findByRole("link", { name: "install the App" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Add repository" }));
  const dialog = within(await screen.findByRole("dialog"));
  const [select] = dialog.getAllByLabelText(/Repository/, { selector: "input" });
  fireEvent.click(select);
  fireEvent.click(await screen.findByRole("option", { name: "o/b", hidden: true }));
  fireEvent.change(dialog.getByLabelText(/Default acceptance checks/), { target: { value: "uv sync\n\nuv run pytest -q\n" } });
  fireEvent.click(dialog.getByRole("button", { name: "Add" }));

  expect(staged).toEqual([[["repos", "o/b"], { default_checks: ["uv sync", "uv run pytest -q"] }]]);
});

test("a listed repository is removed here, one already listed is not offered again, and a staged one says so", async () => {
  const staged = show({ "o/a": { default_checks: ["make test"] }, "o/b": { default_checks: [] } }, { "o/a": { default_checks: ["make test"] } });

  expect(within(screen.getByTestId("repo-o/b")).getByText("Added, not saved yet")).toBeTruthy();
  expect(within(screen.getByTestId("repo-o/a")).queryByText("Added, not saved yet")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Remove o/a" }));
  expect(staged).toEqual([[["repos", "o/a"], undefined]]);

  fireEvent.click(screen.getByRole("button", { name: "Add repository" }));
  expect(await screen.findByText(/Every repository the App is installed on is listed already/)).toBeTruthy();
});
