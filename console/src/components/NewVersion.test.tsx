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
  vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify({ version: "0.1.0", commit }), { status: 200, headers: { "Content-Type": "application/json" } })));
  render(<MantineProvider theme={theme}><NewVersion /></MantineProvider>);
  await act(async () => vi.advanceTimersByTimeAsync(61_000));
  expect(screen.queryByRole("button", { name: "Reload for the new version" })).toBeNull();

  commit = "bbbbbbb";
  await act(async () => vi.advanceTimersByTimeAsync(61_000));
  expect(screen.getByRole("button", { name: "Reload for the new version" })).toBeTruthy();
});
