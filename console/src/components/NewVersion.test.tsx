import { act, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../theme";
import { NewVersion } from "./NewVersion";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

test("a page open across a deploy offers to load the new version, and not before", async () => {
  vi.useFakeTimers();
  let commit = "aaaaaaa";
  let version = "0.1.0";
  vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify({ version, commit }), { status: 200, headers: { "Content-Type": "application/json" } })));
  render(<MantineProvider theme={theme}><NewVersion /></MantineProvider>);
  await act(async () => vi.advanceTimersByTimeAsync(61_000));
  expect(screen.queryByRole("button", { name: /^Reload for/ })).toBeNull();

  commit = "bbbbbbb";
  version = "0.2.0";
  await act(async () => vi.advanceTimersByTimeAsync(61_000));
  // The button names the version it loads, so the operator sees what they are moving to.
  expect(screen.getByRole("button", { name: "Reload for version 0.2.0" })).toBeTruthy();
});
