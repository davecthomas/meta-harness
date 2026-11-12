import { fireEvent, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../theme";
import { call, SIGNED_OUT } from "../client";
import { SignIn } from "../features/auth/SignIn";
import { ErrorAlert } from "./ui";

afterEach(() => vi.unstubAllGlobals());

const show = (node: React.ReactNode) =>
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>{node}</MemoryRouter>
    </MantineProvider>,
  );

test("a failure says what to do: try again, and where to read why", () => {
  show(<ErrorAlert error="GitHub answered 502" />);
  expect(screen.getByText("GitHub answered 502")).toBeTruthy();
  expect(screen.getByText("Reload to try again")).toBeTruthy();
  expect((screen.getByText("Logs") as HTMLAnchorElement).getAttribute("href")).toContain("/admin/logs?q=GitHub");
});

test("a page that knows the remedy gives its own", () => {
  show(<ErrorAlert error="the goal is canceled" remedy={<span>Start a new goal.</span>} />);
  expect(screen.getByText("Start a new goal.")).toBeTruthy();
  expect(screen.queryByText("Reload to try again")).toBeNull();
});

test("a lost session is said plainly and offers to sign in again", () => {
  const heard = vi.fn();
  window.addEventListener(SIGNED_OUT, heard);
  show(<ErrorAlert error="not signed in" />);
  expect(screen.getByText("Your session ended")).toBeTruthy();
  fireEvent.click(screen.getByText("Sign in again"));
  expect(heard).toHaveBeenCalled();
  window.removeEventListener(SIGNED_OUT, heard);
});

test("any request the edge answers 401 tells the app the session ended; a failed sign-in does not", async () => {
  const heard = vi.fn();
  window.addEventListener(SIGNED_OUT, heard);
  vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify({ detail: "not signed in" }), { status: 401 })));
  await expect(call("/v1/goals")).rejects.toThrow("not signed in");
  expect(heard).toHaveBeenCalledTimes(1);
  await expect(call("/auth/google", { method: "POST" })).rejects.toThrow();
  expect(heard).toHaveBeenCalledTimes(1);
  window.removeEventListener(SIGNED_OUT, heard);
});

test("the sign-in screen says the session ended, when it did", () => {
  vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify({ google_client_id: null }), { status: 200 })));
  show(<SignIn onSignedIn={() => undefined} expired />);
  expect(screen.getByTestId("session-ended").textContent).toContain("Sessions last 12 hours");
});
