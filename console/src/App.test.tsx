import { fireEvent, render, screen, within } from "@testing-library/react";
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
  expect(within(screen.getByRole("main")).getByText(/No open goal yet\./)).toBeTruthy();
});

test("an identity outside the access list sees why, and no page", async () => {
  vi.stubGlobal("fetch", () => respond({ detail: "someone@example.com is not on the console's access list" }, 403));

  renderAt("/");

  expect(await screen.findByText("This account may not open the console")).toBeTruthy();
  expect(screen.getByText(/not on the console's access list/)).toBeTruthy();
  // The way out: ask to be invited, or use another account.
  expect(screen.getByText(/Ask an admin to invite this Google account/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Sign out and use another account" })).toBeTruthy();
});

test("a console that cannot be reached says so, apart from an account that is refused", async () => {
  vi.stubGlobal("fetch", () => respond({ detail: "the planner did not answer" }, 502));

  renderAt("/");

  expect(await screen.findByText("The console could not be reached")).toBeTruthy();
  expect(screen.getByText("the planner did not answer")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  expect(screen.queryByText("This account may not open the console")).toBeNull();
});

test("without a session the console shows Google sign-in, and signing in opens the page", async () => {
  let signedIn = false;
  const posted: unknown[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/me") return signedIn ? respond({ identity: "operator@example.com" }) : respond({ detail: "not signed in" }, 401);
    if (url === "/auth/config") return respond({ google_client_id: "client.apps.googleusercontent.com" });
    if (url === "/auth/google") {
      posted.push(JSON.parse(String(init?.body)));
      signedIn = true;
      return respond({ identity: "operator@example.com" });
    }
    if (url.startsWith("/v1/goals")) return respond({ goals: [], total: 0 });
    return respond({ detail: "not stubbed" }, 404);
  });
  // Google Identity Services, as the page sees it: the button calls back with an ID token.
  let callback: ((r: { credential: string }) => void) | null = null;
  vi.stubGlobal("google", {
    accounts: {
      id: {
        initialize: (options: { client_id: string; callback: (r: { credential: string }) => void }) => {
          expect(options.client_id).toBe("client.apps.googleusercontent.com");
          callback = options.callback;
        },
        renderButton: (element: HTMLElement) => {
          element.textContent = "Sign in with Google";
        },
        disableAutoSelect: () => {},
      },
    },
  });

  renderAt("/goals");

  expect(await screen.findByText("Sign in with Google")).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Goals" })).toBeNull();
  callback!({ credential: "google-id-token" });

  expect(await screen.findByRole("heading", { name: "Goals" })).toBeTruthy();
  expect(posted).toEqual([{ credential: "google-id-token" }]);
});

test.each([
  [["operator", "admin"], true],
  [["operator"], false],
])("roles %j: the Admin menu shows only to an admin", async (roles, shown) => {
  vi.stubGlobal("fetch", (url: string) => {
    if (url === "/v1/me") return respond({ identity: "operator@example.com", roles });
    if (url.startsWith("/v1/goals")) return respond({ goals: [], total: 0 });
    return respond({ detail: "not stubbed" }, 404);
  });

  renderAt("/goals");

  expect(await screen.findByRole("heading", { name: "Goals" })).toBeTruthy();
  // The admin pages share one entry at the bottom of the pane: a click opens them in a menu.
  const menu = screen.queryByRole("button", { name: "Admin" });
  expect(!!menu).toBe(shown);
  if (menu) {
    fireEvent.click(menu);
    await screen.findAllByRole("menuitem");
  }
  const items = screen.queryAllByRole("menuitem", { hidden: true }).map((item) => item.textContent);
  for (const name of ["Configuration", "Users", "Deploys", "Audit", "Google Cloud"]) {
    expect(items.includes(name)).toBe(shown);
  }
  // One place for each thing: repositories are under Configuration, and the kill switch is in the header.
  expect(items.includes("Repositories") || items.includes("Kill switch")).toBe(false);
});

test("the header shows the version and the deployed commit, linked", async () => {
  vi.stubGlobal("fetch", (url: string) => {
    if (url === "/v1/me") return respond({ identity: "operator@example.com", roles: ["operator"] });
    if (url === "/v1/version") return respond({ version: "0.21.1", commit: "4a286e3c0ffee" });
    if (url.startsWith("/v1/goals")) return respond({ goals: [], total: 0 });
    return respond({ detail: "not stubbed" }, 404);
  });

  renderAt("/goals");

  expect((await screen.findByTestId("version")).textContent).toBe("v0.21.1 · 4a286e3");
  expect(screen.getByRole("link", { name: "4a286e3" }).getAttribute("href")).toBe("https://github.com/davecthomas/meta-harness/commit/4a286e3c0ffee");
});

test("an address that is no page's says so and offers the ways on", async () => {
  vi.stubGlobal("fetch", (url: string) => {
    if (url === "/v1/me") return respond({ identity: "operator@example.com", roles: ["operator"] });
    return respond({});
  });

  renderAt("/goalz");

  const missing = await screen.findByTestId("no-such-page");
  expect(missing.textContent).toContain("Nothing is at /goalz");
  expect(within(missing).getByRole("link", { name: "Goals" }).getAttribute("href")).toBe("/goals");
});

test("the left menu hides for more room on the page, by its button or the shortcut, and stays hidden next time", async () => {
  window.localStorage.removeItem("mh:menu-hidden");
  vi.stubGlobal("fetch", (url: string) => {
    if (url === "/v1/me") return respond({ identity: "operator@example.com" });
    if (url.startsWith("/v1/goals")) return respond({ goals: [], total: 0 });
    return respond({ detail: "not stubbed" }, 404);
  });
  const first = renderAt("/goals");

  fireEvent.click(await screen.findByRole("button", { name: "Hide the menu" }));
  expect(screen.getByRole("button", { name: "Show the menu" })).toBeTruthy();
  expect(window.localStorage.getItem("mh:menu-hidden")).toBe("1");
  first.unmount();

  renderAt("/goals");
  expect(await screen.findByRole("button", { name: "Show the menu" })).toBeTruthy();
  fireEvent.keyDown(document.documentElement, { key: "\\", code: "Backslash", ctrlKey: true });
  expect(await screen.findByRole("button", { name: "Hide the menu" })).toBeTruthy();
  expect(window.localStorage.getItem("mh:menu-hidden")).toBe("0");
});
