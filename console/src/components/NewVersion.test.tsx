import { act, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { useState } from "react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { theme } from "../theme";
import { NewVersion, RELOADED_FOR, reloader, RETRY_MS } from "./NewVersion";
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

test("a page open across a deploy loads the new version by itself, with nothing to press, and not before", async () => {
  const reload = vi.spyOn(reloader, "reload").mockImplementation(() => undefined);
  show();
  await act(async () => vi.advanceTimersByTimeAsync(61_000));
  expect(reload).not.toHaveBeenCalled();
  await deploy();
  expect(reload).toHaveBeenCalledTimes(1);
  expect(window.sessionStorage.getItem(RELOADED_FOR)).toBe("0.2.0");
  // No button asks for it.
  expect(screen.queryByRole("button", { name: /Load|Reload/ })).toBeNull();
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

test("unsaved changes hold the reload off, and it goes ahead by itself once they are saved", async () => {
  const reload = vi.spyOn(reloader, "reload").mockImplementation(() => undefined);
  show();
  await act(async () => setDirty(true));
  await deploy();
  await act(async () => go("/goals"));
  expect(reload).not.toHaveBeenCalled();
  await act(async () => setDirty(false));
  await act(async () => vi.advanceTimersByTimeAsync(RETRY_MS + 100));
  expect(reload).toHaveBeenCalledTimes(1);
});

test("a field being typed in holds the reload off until the operator leaves it", async () => {
  const reload = vi.spyOn(reloader, "reload").mockImplementation(() => undefined);
  show();
  const field = document.createElement("textarea");
  document.body.appendChild(field);
  field.focus();
  await deploy();
  expect(reload).not.toHaveBeenCalled();
  field.blur();
  await act(async () => vi.advanceTimersByTimeAsync(RETRY_MS + 100));
  expect(reload).toHaveBeenCalledTimes(1);
  field.remove();
});

test("a page that reloaded itself says so once it is back", async () => {
  vi.useRealTimers();
  window.sessionStorage.setItem(RELOADED_FOR, "0.2.0");
  show();
  expect((await screen.findByTestId("updated-note")).textContent).toBe("Updated to version 0.2.0");
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
