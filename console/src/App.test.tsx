import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { App } from "./App";
import { theme } from "./theme";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

function renderAt(path: string) {
  return render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("the shell signs in, shows the navigation, and opens the page for the path", async () => {
  vi.stubGlobal("fetch", (url: string) => {
    if (url === "/v1/me") return respond({ identity: "operator@example.com" });
    if (url.startsWith("/v1/goals")) return respond({ goals: [], total: 0 });
    return respond({ detail: "not stubbed" }, 404);
  });

  renderAt("/goals");

  expect(await screen.findByRole("heading", { name: "Goals" })).toBeTruthy();
  for (const name of ["Overview", "Goals", "Tasks", "New goal"]) {
    expect(screen.getAllByRole("link", { name }).length).toBeGreaterThan(0);
  }
  expect(await screen.findByText("No open goals.")).toBeTruthy();
});

test("an identity outside the access list sees why, and no page", async () => {
  vi.stubGlobal("fetch", () => respond({ detail: "someone@example.com is not on the console's access list" }, 403));

  renderAt("/");

  expect(await screen.findByText("Not allowed")).toBeTruthy();
  expect(screen.getByText(/not on the console's access list/)).toBeTruthy();
});
