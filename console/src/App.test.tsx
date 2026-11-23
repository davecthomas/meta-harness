import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { App, HEADER_HEIGHT, LOGO_HEIGHT } from "./App";
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
  // New goal sits just above Goals, and Help at the foot of the pane, after every page's entry.
  const pane = Array.from(document.querySelectorAll("nav a")).map((a) => a.textContent);
  expect(pane.indexOf("New goal")).toBe(pane.indexOf("Goals") - 1);
  expect(pane.indexOf("Help")).toBeGreaterThan(pane.indexOf("Tasks"));
  expect(pane.filter((name) => name === "Help")).toHaveLength(1);
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

test("the footer ends a signed-in page with the version and the deployed commit, linked, and the header has neither", async () => {
  vi.stubGlobal("fetch", (url: string) => {
    if (url === "/v1/me") return respond({ identity: "operator@example.com", roles: ["operator"] });
    if (url === "/v1/version") return respond({ version: "0.21.1", commit: "4a286e3c0ffee" });
    if (url.startsWith("/v1/goals")) return respond({ goals: [], total: 0 });
    return respond({ detail: "not stubbed" }, 404);
  });

  renderAt("/goals");

  const footer = within(screen.getByTestId("footer"));
  expect((await footer.findByTestId("version")).textContent).toBe("v0.21.1 · 4a286e3");
  expect(footer.getByRole("link", { name: "4a286e3" }).getAttribute("href")).toBe("https://github.com/davecthomas/meta-harness/commit/4a286e3c0ffee");
  expect(footer.getByRole("link", { name: "GitHub" }).getAttribute("href")).toBe("https://github.com/davecthomas/meta-harness");
  expect(footer.getByText("The Open Meta-Harness for Coding at Scale")).toBeTruthy();
  // The footer comes after the page's content, inside the main area.
  const main = screen.getByRole("main");
  expect(main.lastElementChild).toBe(screen.getByTestId("footer"));
  const header = within(screen.getByRole("banner"));
  expect(header.queryByTestId("version")).toBeNull();
  expect(header.queryByText(/v0\.21\.1/)).toBeNull();
  expect(header.queryByRole("link", { name: "4a286e3" })).toBeNull();
});

test("the sign-in screen ends with the footer, without the version, which needs a session", async () => {
  const asked: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    asked.push(url);
    if (url === "/v1/me") return respond({ detail: "not signed in" }, 401);
    if (url === "/auth/config") return respond({ google_client_id: "client.apps.googleusercontent.com" });
    return respond({ detail: "not stubbed" }, 404);
  });
  vi.stubGlobal("google", {
    accounts: { id: { initialize: () => {}, renderButton: () => {}, disableAutoSelect: () => {} } },
  });

  renderAt("/");

  const footer = within(await screen.findByTestId("footer"));
  expect(footer.getByRole("img", { name: "Octofusion AI" })).toBeTruthy();
  expect(footer.getByText("The Open Meta-Harness for Coding at Scale")).toBeTruthy();
  expect(footer.getByRole("link", { name: "GitHub" }).getAttribute("href")).toBe("https://github.com/davecthomas/meta-harness");
  expect(screen.getByTestId("google-sign-in")).toBeTruthy();
  // Signed out, the version is not asked for: the edge would refuse it.
  expect(asked).not.toContain("/v1/version");
  expect(footer.queryByTestId("version")).toBeNull();
});

function stubSignedIn() {
  vi.stubGlobal("fetch", (url: string) => {
    if (url === "/v1/me") return respond({ identity: "operator@example.com", roles: ["operator"] });
    if (url.startsWith("/v1/goals")) return respond({ goals: [], total: 0 });
    return respond({ detail: "not stubbed" }, 404);
  });
}

test("the header has a link to the Overview named Octofusion AI, and no Meta-harness name", async () => {
  stubSignedIn();
  renderAt("/goals");
  await screen.findByRole("heading", { name: "Goals" });
  const header = within(screen.getByRole("banner"));
  expect(header.getByRole("link", { name: "Octofusion AI" }).getAttribute("href")).toBe("/");
  expect(header.queryByText("Meta-harness")).toBeNull();
});

test("the header shows the lockup at wide widths and the symbol alone at phone width", async () => {
  stubSignedIn();
  renderAt("/goals");
  await screen.findByRole("heading", { name: "Goals" });
  const images = within(screen.getByRole("banner")).getAllByRole("img", { name: "Octofusion AI" });
  expect(images).toHaveLength(2);
  const [wide, phone] = images;
  expect(wide.getAttribute("src")).toContain("octofusion-lockup.svg");
  expect(wide.className).toContain("mantine-visible-from-sm");
  expect(phone.getAttribute("src")).toContain("octofusion-symbol.svg");
  expect(phone.className).toContain("mantine-hidden-from-sm");
  // Large enough to read: at 32 pixels the name's letters were 7 pixels tall.
  expect(LOGO_HEIGHT).toBeGreaterThanOrEqual(44);
  expect(HEADER_HEIGHT).toBeGreaterThan(LOGO_HEIGHT);
  for (const image of images) expect(image.style.getPropertyValue("--image-height") || image.style.height).toContain(`${LOGO_HEIGHT / 16}rem`);
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
