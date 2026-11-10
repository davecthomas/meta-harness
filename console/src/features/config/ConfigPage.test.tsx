import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { ConfigPage } from "./ConfigPage";
import { theme } from "../../theme";

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
    if (url === "/v1/config/profiles") return respond({ ...VERSION, kind: "profiles", document: { profiles: { "pi-lean": {}, "pi-careful": {} } } });
    if (url === "/v1/config/models") return respond({ models: [{ name: "claude:claude-sonnet-5" }, { name: "openai-responses:gpt-6-sol" }] });
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

test("a save the service refuses shows each reason beside the tier it names, and the rest on top", async () => {
  stub(() => respond({ detail: { reasons: ["tier standard has no candidates", "default_tier is required"] } }, 422));
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(await screen.findByLabelText("Description"), { target: { value: "One module, clear checks." } });
  fireEvent.click(screen.getByRole("button", { name: "Review and save" }));
  fireEvent.click(await screen.findByRole("button", { name: "Save as version 2" }));

  expect(await screen.findByText("Not saved: the service would refuse this version")).toBeTruthy();
  expect(screen.getByText("default_tier is required")).toBeTruthy();
  const tier = screen.getByTestId("tier-standard");
  expect(within(tier).getByText("tier standard has no candidates")).toBeTruthy();
});

test("the tier form changes a candidate's model, and the review shows the change before it saves with its note", async () => {
  const saved: unknown[] = [];
  stub(() => {
    return respond({ ...VERSION, version: 2 });
  });
  const realFetch = globalThis.fetch;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (init?.method === "PUT") saved.push(JSON.parse(String(init.body)));
    return realFetch(url, init);
  });
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  const [model] = await screen.findAllByLabelText("Model of candidate 1 in standard", { selector: "input" });
  fireEvent.focus(model);
  fireEvent.change(model, { target: { value: "gpt-6" } });
  fireEvent.click(await screen.findByText("openai-responses:gpt-6-sol"));
  fireEvent.click(screen.getByRole("button", { name: "Review and save" }));

  expect(await screen.findByText(/\+ .*harness:pi-lean\/openai-responses:gpt-6-sol/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "Sol first" } });
  fireEvent.click(screen.getByRole("button", { name: "Save as version 2" }));

  await waitFor(() => expect(saved).toHaveLength(1));
  expect(saved[0]).toMatchObject({ note: "Sol first", document: { tiers: [{ models: ["harness:pi-lean/openai-responses:gpt-6-sol"] }] } });
});
