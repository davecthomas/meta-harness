import { act, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { useState } from "react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { theme } from "../theme";
import { NewVersion, RELOADED_FOR, reloader } from "./NewVersion";
import { useUnsavedChanges } from "./UnsavedChanges";

let commit = "aaaaaaa";
let version = "0.1.0";
let hidden = false;

beforeEach(() => {
  commit = "aaaaaaa";
  version = "0.1.0";
  hidden = false;
  vi.useFakeTimers();
  vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify({ version, commit }), { status: 200, headers: { "Content-Type": "application/json" } })));
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

let go: (to: string) => void = () => undefined;
function Mover() {
  go = useNavigate();
  return null;
}
let setDirty: (dirty: boolean) => void = () => undefined;
function Editor() {
  const [dirty, set] = useState(false);
  setDirty = set;
  const unsaved = useUnsavedChanges({ dirty, describe: "a draft", save: async () => true, discard: () => undefined });
  return <>{unsaved.element}</>;
}

function show() {
  render(
    <MantineProvider theme={theme}>
      <Notifications />
      <MemoryRouter>
        <Mover />
        <Editor />
        <NewVersion />
      </MemoryRouter>
    </MantineProvider>,
  );
}

const deploy = async () => {
  commit = "bbbbbbb";
  version = "0.2.0";
  await act(async () => vi.advanceTimersByTimeAsync(61_000));
};

test("a page open across a deploy offers the new version, and not before", async () => {
  const reload = vi.spyOn(reloader, "reload").mockImplementation(() => undefined);
  show();
  await act(async () => vi.advanceTimersByTimeAsync(61_000));
  expect(screen.queryByRole("button", { name: /^Load/ })).toBeNull();
  await deploy();
  // The button names the version it loads. Nothing reloads under the operator while they read the page.
  expect(screen.getByRole("button", { name: "Load version 0.2.0" })).toBeTruthy();
  expect(reload).not.toHaveBeenCalled();
});

test("the new version loads by itself on the next move to another page", async () => {
  const reload = vi.spyOn(reloader, "reload").mockImplementation(() => undefined);
  show();
  await deploy();
  await act(async () => go("/goals"));
  expect(reload).toHaveBeenCalledTimes(1);
  expect(window.sessionStorage.getItem(RELOADED_FOR)).toBe("0.2.0");
});

test("a page that reloaded itself says so once it is back", async () => {
  vi.useRealTimers();
  window.sessionStorage.setItem(RELOADED_FOR, "0.2.0");
  show();
  expect(await screen.findByText("The page reloaded to load version 0.2.0.")).toBeTruthy();
  expect(screen.getByText("Muxxy was updated")).toBeTruthy();
  expect(window.sessionStorage.getItem(RELOADED_FOR)).toBeNull();
});

test("a tab left open loads the new version when the operator comes back to it", async () => {
  const reload = vi.spyOn(reloader, "reload").mockImplementation(() => undefined);
  show();
  hidden = true;
  await deploy();
  expect(reload).not.toHaveBeenCalled();
  hidden = false;
  await act(async () => void document.dispatchEvent(new Event("visibilitychange")));
  expect(reload).toHaveBeenCalledTimes(1);
});

test("unsaved changes hold the reload off, and the button stays", async () => {
  const reload = vi.spyOn(reloader, "reload").mockImplementation(() => undefined);
  show();
  await act(async () => setDirty(true));
  await deploy();
  await act(async () => go("/goals"));
  expect(reload).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Load version 0.2.0" })).toBeTruthy();
});
