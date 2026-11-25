import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { GitHubConnected, GitHubSection } from "./GitHubConnect";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
const CONNECTION = { enabled: true, why_off: null, state: "none", connected: false, login: null, scopes: "", connected_at: null };

function Where() {
  const at = useLocation();
  return <span data-testid="where">{at.pathname + at.search}</span>;
}

function show(ui: React.ReactNode, at = "/settings") {
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route path="/github/connected" element={ui} />
          <Route path="*" element={<>{at.startsWith("/github") ? null : ui}<Where /></>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("an operator with no connection gets one button, which sends the browser to GitHub", async () => {
  const went: string[] = [];
  vi.stubGlobal("location", { ...window.location, pathname: "/goals/new", search: "?step=3", assign: (url: string) => went.push(url) });
  vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => (init?.method === "POST" ? json({ url: "https://github.com/login/oauth/authorize?client_id=cid&state=n" }) : json(CONNECTION)));
  show(<GitHubSection />);

  fireEvent.click(await screen.findByRole("button", { name: "Connect GitHub" }));

  await waitFor(() => expect(went).toEqual(["https://github.com/login/oauth/authorize?client_id=cid&state=n"]));
  expect(window.sessionStorage.getItem("mh:github-return-to")).toBe("/goals/new?step=3");
});

test("a connection says who it is as, and a broken one says to reconnect", async () => {
  vi.stubGlobal("fetch", () => json({ ...CONNECTION, state: "connected", connected: true, login: "octo-dave", scopes: "repo" }));
  show(<GitHubSection />);
  expect((await screen.findByTestId("github-section")).textContent).toContain("Connected as octo-dave");
  expect(screen.getByRole("button", { name: "Disconnect" })).toBeTruthy();
  cleanup();

  vi.stubGlobal("fetch", () => json({ ...CONNECTION, state: "broken", login: "octo-dave" }));
  show(<GitHubSection />);
  expect((await screen.findByTestId("github-section")).textContent).toContain("GitHub no longer accepts the connection as octo-dave");
  expect(screen.getByRole("button", { name: "Reconnect GitHub" })).toBeTruthy();
});

test("a deployment with no OAuth app says why connecting is off, and offers no button that cannot work", async () => {
  vi.stubGlobal("fetch", () => json({ ...CONNECTION, enabled: false, why_off: "connecting GitHub is not set up on this deployment. An admin sets three settings, then deploys" }));
  show(<GitHubSection />);
  expect((await screen.findByTestId("github-section")).textContent).toContain("An admin sets three settings");
  expect(screen.queryByRole("button", { name: "Connect GitHub" })).toBeNull();
});

test("back from GitHub, the code is handed over and the operator returns to where they were", async () => {
  const sent: unknown[] = [];
  window.sessionStorage.setItem("mh:github-return-to", "/goals/new?step=3");
  vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => {
    sent.push(JSON.parse(String(init?.body)));
    return json({ ...CONNECTION, state: "connected", connected: true, login: "octo-dave" });
  });
  show(<GitHubConnected />, "/github/connected?code=abc&state=n1");

  await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/goals/new?step=3"));
  expect(sent).toEqual([{ code: "abc", state: "n1" }]);
});

test("a sign-in GitHub refused, or the service refused, says why and offers to connect again", async () => {
  show(<GitHubConnected />, "/github/connected?error=access_denied&error_description=The+user+denied+access");
  expect((await screen.findByRole("alert")).textContent).toContain("GitHub did not connect: The user denied access.");
  expect(screen.getByRole("button", { name: "Connect GitHub" })).toBeTruthy();
  cleanup();

  vi.stubGlobal("fetch", () => json({ detail: "this sign-in was not started from your console session. Press Connect GitHub to start again" }, 409));
  show(<GitHubConnected />, "/github/connected?code=abc&state=stolen");
  expect((await screen.findByRole("alert")).textContent).toContain("was not started from your console session");
});
