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

function stub(onPut: (body: any) => Promise<Response>) {
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
    if (url === "/v1/config/tiers" && init?.method === "PUT") return onPut(JSON.parse(String(init.body)));
    if (url === "/v1/config/tiers") return respond(VERSION);
    if (url === "/v1/config/tiers/versions") return respond({ versions: [VERSION] });
    if (url === "/v1/config/profiles/versions") return respond({ versions: [] });
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

test("the tiers tab lays out each tier with its thinking level and its candidates in order, with no Edit button", async () => {
  stub(() => respond({}));
  renderPage();

  expect(await screen.findByText("thinking medium")).toBeTruthy();
  expect(screen.getByText("Tier 1")).toBeTruthy();
  expect(screen.getByText("Escalate one tier up")).toBeTruthy();
  expect(screen.getByText("pi-lean")).toBeTruthy();
  expect(screen.getByText("claude:claude-sonnet-5")).toBeTruthy();
  expect(screen.getByRole("tab", { name: /Harness profiles/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
});

test("an edit in place is staged and marked, and Review and save saves every change as the next version with its note", async () => {
  const saved: any[] = [];
  stub((body) => {
    saved.push(body);
    return respond({ ...VERSION, version: 2 });
  });
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "Edit Description of standard" }));
  const description = screen.getByLabelText("Description of standard");
  fireEvent.change(description, { target: { value: "One module, clear checks." } });
  fireEvent.keyDown(description, { key: "Enter", ctrlKey: true });

  expect(await screen.findByText("1 change to version 1")).toBeTruthy();
  expect(screen.getByText("changed")).toBeTruthy();
  expect(saved).toHaveLength(0);

  fireEvent.click(screen.getByRole("button", { name: "Edit Model of candidate 1 in standard" }));
  fireEvent.click(await screen.findByText("openai-responses:gpt-6-sol"));
  expect(await screen.findByText("2 changes to version 1")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Review and save" }));
  expect(await screen.findByText(/\+ .*harness:pi-lean\/openai-responses:gpt-6-sol/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "Sol first" } });
  fireEvent.click(screen.getByRole("button", { name: "Save as version 2" }));

  await waitFor(() => expect(saved).toHaveLength(1));
  expect(saved[0]).toMatchObject({
    note: "Sol first",
    document: { tiers: [{ description: "One module, clear checks.", models: ["harness:pi-lean/openai-responses:gpt-6-sol"] }] },
  });
});

test("a refused save shows each reason beside the value it is about, the rest on top, and the changes stay staged", async () => {
  stub(() => respond({ detail: { reasons: ["tier standard has no candidates", "default_tier is required: the router needs a tier", "the router does not parse: x"] } }, 422));
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "Remove candidate 1 in standard" }));
  expect(await screen.findByText("1 change to version 1")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Review and save" }));
  fireEvent.click(await screen.findByRole("button", { name: "Save as version 2" }));

  expect(await screen.findByText("Not saved: the service would refuse this version")).toBeTruthy();
  expect(screen.getByText("the router does not parse: x")).toBeTruthy();
  expect(within(screen.getByTestId("tier-standard")).getByText("tier standard has no candidates")).toBeTruthy();
  expect(screen.getByText("default_tier is required: the router needs a tier")).toBeTruthy();
  expect(screen.getByText("1 change to version 1")).toBeTruthy();
});

test("Discard drops the staged changes, and Advanced: JSON opens the document with them", async () => {
  stub(() => respond({}));
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "Edit When a tier is used up" }));
  fireEvent.click(await screen.findByRole("option", { name: "Fail", hidden: true }));
  expect(await screen.findByText("1 change to version 1")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Advanced: JSON" }));
  const json = (await screen.findByLabelText(/JSON/, { selector: "textarea" })) as HTMLTextAreaElement;
  expect(json.value).toContain('"on_exhausted": "fail"');
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

  fireEvent.click(screen.getByRole("button", { name: "Discard" }));
  await waitFor(() => expect(screen.queryByText("1 change to version 1")).toBeNull());
  expect(screen.getByText("Escalate one tier up")).toBeTruthy();
});

test("a profile is added and removed from the table, each staged", async () => {
  stub(() => respond({}));
  renderPage();

  fireEvent.click(await screen.findByRole("tab", { name: /Harness profiles/ }));
  fireEvent.change(await screen.findByLabelText("New profile name"), { target: { value: "pi-fast" } });
  fireEvent.click(screen.getByRole("button", { name: "Add profile" }));
  expect(await screen.findByTestId("profile-pi-fast")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Remove profile pi-careful" }));

  expect(await screen.findByText("2 changes to version 1")).toBeTruthy();
  expect(screen.queryByTestId("profile-pi-careful")).toBeNull();
});
