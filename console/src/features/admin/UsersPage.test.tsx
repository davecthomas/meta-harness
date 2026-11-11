import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { theme } from "../../theme";
import { describeAudit } from "./AdminPages";
import { UsersPage, inviteText } from "./UsersPage";

afterEach(() => vi.unstubAllGlobals());

const USERS = [
  { email: "dave@example.com", role: "admin", status: "active", invited_by: "edge", invited_at: "2026-11-01T10:00:00Z", last_sign_in_at: "2026-11-11T09:00:00Z" },
  { email: "pat@example.com", role: "user", status: "invited", invited_by: "dave@example.com", invited_at: "2026-11-11T10:00:00Z", last_sign_in_at: null },
];

function show() {
  const asked: { method: string; url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method !== "GET") asked.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null });
    const answer = url === "/v1/me" ? { identity: "dave@example.com", roles: ["operator", "admin"] } : method === "GET" ? { users: USERS } : USERS[1];
    return Promise.resolve(new Response(JSON.stringify(answer), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(<MantineProvider theme={theme}><MemoryRouter><UsersPage /></MemoryRouter></MantineProvider>);
  return asked;
}

test("the users list shows each user, and the only admin cannot be changed", async () => {
  show();
  const dave = await screen.findByTestId("user-dave@example.com");
  expect(dave.textContent).toContain("(you)");
  expect(dave.textContent).toContain("The only admin");
  expect((within(dave).getByLabelText("Role of dave@example.com") as HTMLSelectElement).disabled).toBe(true);
  expect((within(dave).getByRole("button", { name: "Deprovision" }) as HTMLButtonElement).disabled).toBe(true);
  const pat = screen.getByTestId("user-pat@example.com");
  expect(pat.textContent).toContain("invited");
  expect(pat.textContent).toContain("Never");
});

test("an admin invites a user, sets a role, and ends access with a second click", async () => {
  const asked = show();
  await screen.findByTestId("user-pat@example.com");

  fireEvent.change(screen.getByLabelText("Invite a user"), { target: { value: "sam@example.com" } });
  fireEvent.change(screen.getByLabelText("Role"), { target: { value: "admin" } });
  fireEvent.click(screen.getByRole("button", { name: "Invite" }));
  await waitFor(() => expect(asked).toHaveLength(1));

  fireEvent.change(screen.getByLabelText("Role of pat@example.com"), { target: { value: "admin" } });
  await waitFor(() => expect(asked).toHaveLength(2));

  const pat = screen.getByTestId("user-pat@example.com");
  fireEvent.click(within(pat).getByRole("button", { name: "Remove" }));
  expect(asked).toHaveLength(2);
  fireEvent.click(within(pat).getByRole("button", { name: "Confirm: end their access" }));
  await waitFor(() => expect(asked).toHaveLength(3));

  expect(asked).toEqual([
    { method: "POST", url: "/v1/admin/users", body: { email: "sam@example.com", role: "admin" } },
    { method: "PATCH", url: "/v1/admin/users/pat%40example.com", body: { role: "admin" } },
    { method: "DELETE", url: "/v1/admin/users/pat%40example.com", body: null },
  ]);
});

test("an invitation is text to send by hand, and each change reads plainly in the audit", () => {
  expect(inviteText("pat@example.com", "dave@example.com", "https://console.example")).toBe(
    "dave@example.com invited you to the meta-harness console. Sign in at https://console.example with your Google account, pat@example.com.",
  );
  const event = (event_type: string, payload: Record<string, unknown>) => ({ event_type, payload }) as Parameters<typeof describeAudit>[0];
  expect(describeAudit(event("user_invited", { email: "pat@example.com", role: "user" }))).toBe("Invited pat@example.com as a regular user");
  expect(describeAudit(event("user_role_changed", { email: "pat@example.com", to: "admin" }))).toBe("Made pat@example.com an admin");
  expect(describeAudit(event("user_deprovisioned", { email: "pat@example.com" }))).toBe("Ended pat@example.com's access");
});
