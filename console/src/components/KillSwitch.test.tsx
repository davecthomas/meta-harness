import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { afterEach, expect, test, vi } from "vitest";
import { forget, KillSwitchBanner, KillSwitchButton } from "./KillSwitch";
import { theme } from "../theme";

// The kill switch is on every page: an admin can stop everything from the header, and while it is on a
// banner says so, with who did it and why, and the way back.

afterEach(() => {
  vi.unstubAllGlobals();
  forget();
});

function respond(body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
}

const OFF = { engaged: false, draining: false, changed_by: "migration", reason: "starts off", changed_at: "2026-11-08T10:16:00Z" };

function fakeSwitch(start: object) {
  let state = { ...OFF, ...start };
  const posted: unknown[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url === "/v1/kill-switch" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      posted.push(body);
      state = { ...state, engaged: body.engaged ?? body.mode === "stop", draining: body.mode === "drain", changed_by: "dave", reason: body.reason };
    }
    return respond(state);
  });
  return posted;
}

function show(admin: boolean) {
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        {admin && <KillSwitchButton />}
        <KillSwitchBanner admin={admin} />
      </MemoryRouter>
    </MantineProvider>,
  );
}

test("while the switch is off an admin sees Stop everything, which asks first and then stops, and the banner shows", async () => {
  const posted = fakeSwitch({});
  show(true);

  fireEvent.click(await screen.findByRole("button", { name: "Stop everything" }));
  expect(await screen.findByText("Stop everything?")).toBeTruthy();
  expect(posted).toEqual([]);
  expect(screen.queryByTestId("kill-switch-banner")).toBeNull();

  fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "runaway spend" } });
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Stop everything" }));

  expect((await screen.findByTestId("kill-switch-banner")).textContent).toContain("Kill switch is on: nothing runs");
  expect(screen.getByTestId("kill-switch-banner").textContent).toContain("runaway spend");
  expect(posted).toEqual([{ engaged: true, reason: "runaway spend" }]);
  expect(screen.getByRole("button", { name: "Lift kill switch" })).toBeTruthy();
});

test("an admin lifts the switch from the banner", async () => {
  const posted = fakeSwitch({ engaged: true, changed_by: "dave", reason: "runaway spend" });
  show(true);

  fireEvent.click(await screen.findByRole("button", { name: "Lift the kill switch" }));
  expect(await screen.findByText(/Queued tasks start again/)).toBeTruthy();
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Lift the kill switch" }));

  expect(await screen.findByRole("button", { name: "Stop everything" })).toBeTruthy();
  expect(screen.queryByTestId("kill-switch-banner")).toBeNull();
  expect(posted).toEqual([{ engaged: false, reason: "lifted" }]);
});

test("someone who is not an admin sees the banner, with who can lift it, and no control", async () => {
  fakeSwitch({ engaged: true, changed_by: "dave", reason: "runaway spend" });
  show(false);

  expect((await screen.findByTestId("kill-switch-banner")).textContent).toContain("An admin can lift it");
  expect(screen.queryByRole("button")).toBeNull();
});

test("a drain shows its own banner, and ending it by hand says to check for a running deploy first", async () => {
  const posted = fakeSwitch({ draining: true, changed_by: "deploy", reason: "replacing the VM" });
  show(true);

  expect((await screen.findByTestId("kill-switch-banner")).textContent).toContain("Draining for a deploy");
  fireEvent.click(screen.getByRole("button", { name: "End the drain" }));
  expect(await screen.findByText(/only if no deploy is running/)).toBeTruthy();
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "End the drain" }));
  await screen.findByRole("button", { name: "Stop everything" });
  expect(posted).toEqual([{ mode: "off", reason: "drain ended from the console" }]);
});
