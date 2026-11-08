import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { ConfigPage } from "./pages/ConfigPage";
import { theme } from "./theme";

const TIERS = {
  tiers: [
    { name: "standard", description: "One module.", models: ["harness:pi-lean/claude:claude-sonnet-5"], thinking: "medium" },
  ],
  classifier: { engine: "openai-decisions", min_confidence: 0.7 },
  default_tier: "standard",
  on_exhausted: "escalate",
};
const VERSION = { kind: "tiers", version: 1, document: TIERS, edited_by: "seed", note: null, origin: "seed", rolled_back_from: null, created_at: "2026-11-07T23:00:00Z" };

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

function stub(onPut: () => Promise<Response>) {
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/config") {
      return respond({
        source: "db",
        kinds: [
          { kind: "tiers", title: "Router tiers", description: "The router's tiers.", version: 1, edited_by: "seed", created_at: VERSION.created_at },
          { kind: "profiles", title: "Harness profiles", description: "Profiles.", version: 1, edited_by: "seed", created_at: VERSION.created_at },
        ],
      });
    }
    if (url === "/v1/config/tiers" && init?.method === "PUT") return onPut();
    if (url === "/v1/config/tiers") return respond(VERSION);
    if (url === "/v1/config/tiers/versions") return respond({ versions: [VERSION] });
    return respond({ detail: "not stubbed" }, 404);
  });
}

function renderPage() {
  return render(
    <MantineProvider theme={theme}>
      <MemoryRouter initialEntries={["/config"]}>
        <ConfigPage />
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("the tiers tab lays out each tier with its thinking level and its candidates in order", async () => {
  stub(() => respond({}));
  renderPage();

  expect(await screen.findByText("thinking medium")).toBeTruthy();
  expect(screen.getByText("Tier 1")).toBeTruthy();
  expect(screen.getByText("Escalate one tier up")).toBeTruthy();
  expect(screen.getByText("pi-lean")).toBeTruthy();
  expect(screen.getByText("claude:claude-sonnet-5")).toBeTruthy();
  expect(screen.getByRole("tab", { name: /Harness profiles/ })).toBeTruthy();
});

test("a save the service refuses shows every reason, and nothing changes", async () => {
  stub(() => respond({ detail: { reasons: ["tier standard has no candidates", "default_tier is required"] } }, 422));
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: "Save as version 2" }));

  expect(await screen.findByText("Not saved: the service would refuse this version")).toBeTruthy();
  expect(screen.getByText("tier standard has no candidates")).toBeTruthy();
  expect(screen.getByText("default_tier is required")).toBeTruthy();
});
