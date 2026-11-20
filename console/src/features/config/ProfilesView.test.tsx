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

const PROGRAMS = { image: "harness-pi:dev", programs: ["cat", "cd", "git", "ls", "python3", "rg"] };

function show(
  images: () => Promise<Response>,
  profile: Record<string, any> = { tools: ["read"], max_turns: 80, max_minutes: 45, image: null },
  programs: (url: string) => Promise<Response> = () => respond(PROGRAMS),
) {
  vi.stubGlobal("fetch", (url: string) =>
    url === "/v1/config/images" ? images() : url.startsWith("/v1/config/images/programs") ? programs(url) : respond({ detail: "not stubbed" }, 404),
  );
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

// The bash allowlist is picked from the programs the profile's image has (GET /v1/config/images/programs).

const LEAN = { tools: ["bash"], max_turns: 80, max_minutes: 45, image: null };

function openAllowlist() {
  fireEvent.click(screen.getByRole("button", { name: "Edit Bash allowlist of pi-lean" }));
  return screen.getAllByLabelText("Bash allowlist of pi-lean", { selector: "input" })[0];
}

function add(input: HTMLElement, name: string) {
  fireEvent.change(input, { target: { value: name } });
  fireEvent.keyDown(input, { key: "Enter" });
}

test("the programs of the profile's image are offered, and the picked ones are staged", async () => {
  const asked: string[] = [];
  const staged = show(() => respond(IMAGES), { ...LEAN, image: "harness-pi:dev" }, (url) => {
    asked.push(url);
    return respond(PROGRAMS);
  });
  const input = openAllowlist();
  await waitFor(() => expect(input.getAttribute("placeholder")).toBe("Pick a program, Enter to add; Enter again to save"));
  expect(asked).toEqual(["/v1/config/images/programs?image=harness-pi%3Adev"]);
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "py" } });
  expect(await screen.findByRole("option", { name: "python3", hidden: true })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "git", hidden: true })).toBeNull();
  add(input, "GIT");
  add(input, "ls");
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(staged).toEqual([[["profiles", "pi-lean", "bash_allow"], ["git", "ls"]]]));
});

test("a program the image lacks is refused, and a saved one is marked", async () => {
  const staged = show(() => respond(IMAGES), { ...LEAN, bash_allow: ["git", "kubectl"] });
  expect((await screen.findByTestId("bash-unknown-pi-lean")).textContent).toBe("The harness image harness-pi:dev has no program named kubectl");
  const input = openAllowlist();
  add(input, "gti");
  fireEvent.keyDown(input, { key: "Enter" });
  expect(await screen.findByText("The harness image harness-pi:dev has no program named kubectl, gti: pick each from the list")).toBeTruthy();
  expect(staged).toEqual([]);
});

test("when the programs cannot be listed the field says why and still takes typed names", async () => {
  const staged = show(() => respond(IMAGES), LEAN, () =>
    respond({ detail: "the harness image harness-pi:dev is not built on this machine yet: No such image" }, 422),
  );
  expect(
    await screen.findByText(/The programs of the default image could not be listed, so type each exactly: the harness image harness-pi:dev is not built/),
  ).toBeTruthy();
  const input = openAllowlist();
  expect(input.getAttribute("placeholder")).toBe("Type each program's exact name");
  add(input, "kubectl");
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(staged).toEqual([[["profiles", "pi-lean", "bash_allow"], ["kubectl"]]]));
});
