import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { ProfilesView } from "./ProfilesView";
import { theme } from "../../theme";

// A profile's image is picked from the harness images the deployment builds (GET /v1/config/images): the one
// image `make images` builds.

const IMAGES = {
  images: [{ name: "harness-pi:dev", directory: "images/harness-pi", default: true }],
  default: "harness-pi:dev",
};

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function show(images: () => Promise<Response>, profile: Record<string, any> = { tools: ["read"], max_turns: 80, max_minutes: 45, image: null }) {
  vi.stubGlobal("fetch", (url: string) => (url === "/v1/config/images" ? images() : respond({ detail: "not stubbed" }, 404)));
  const document = { profiles: { "pi-lean": profile } };
  const staged: [unknown[], any][] = [];
  render(
    <MantineProvider theme={theme}>
      <ProfilesView staging={{ document, original: document, stage: (path, value) => staged.push([path, value]), reasons: () => [] }} />
    </MantineProvider>,
  );
  return staged;
}

function open() {
  fireEvent.click(screen.getByRole("button", { name: "Edit Image of pi-lean" }));
  return screen.getByRole("combobox", { name: "Image of pi-lean" });
}

test("the images the deployment builds are offered, and an empty image is the default", async () => {
  const staged = show(() => respond(IMAGES));
  expect(await screen.findByText("default (harness-pi:dev)")).toBeTruthy();
  const input = open();
  expect(input.getAttribute("placeholder")).toBe("Pick a harness image");
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "harness" } });
  expect(await screen.findByRole("option", { name: "harness-pi:dev" })).toBeTruthy();
  fireEvent.change(input, { target: { value: "HARNESS-PI:dev" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(staged).toEqual([[["profiles", "pi-lean", "image"], "harness-pi:dev"]]));
});

test("an image the deployment does not build is refused, and a saved one is marked", async () => {
  const staged = show(() => respond(IMAGES), { tools: ["read"], max_turns: 80, max_minutes: 45, image: "harness-gone:dev" });
  expect((await screen.findByTestId("image-unknown-pi-lean")).textContent).toBe("The deployment has no harness image named harness-gone:dev");
  const input = open();
  fireEvent.change(input, { target: { value: "harness-typo:dev" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(await screen.findByText(/The deployment has no harness image named harness-typo:dev: pick one from the list/)).toBeTruthy();
  expect(staged).toEqual([]);
});

test("when the images cannot be listed the field says why and still takes a typed image", async () => {
  const staged = show(() => respond({ detail: "the harness image's build directory /opt/meta-harness/images/harness-pi has no Dockerfile" }, 422));
  expect(await screen.findByText(/The harness images could not be listed, so type each exactly: the harness image's build directory/)).toBeTruthy();
  const input = open();
  expect(input.getAttribute("placeholder")).toBe("Type its name");
  fireEvent.change(input, { target: { value: " harness-mine:dev " } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(staged).toEqual([[["profiles", "pi-lean", "image"], "harness-mine:dev"]]));
});
