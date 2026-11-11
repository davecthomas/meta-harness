import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Group, NativeSelect, Paper, Stack, Table, Text, TextInput } from "@mantine/core";
import { IconUserPlus } from "@tabler/icons-react";
import { api, type ConsoleUser } from "../../api";
import { DataTable, Empty, ErrorAlert, PageHeader, when } from "../../components/ui";

// The console's users, for an admin: invite one, set each one's role, and end one's access. There is always an
// admin: the last one's row says why it cannot be changed, and the server refuses it too.

const ROLES = [{ value: "user", label: "Regular user" }, { value: "admin", label: "Admin" }];
const STATUS_COLOR: Record<string, string> = { active: "teal", invited: "blue", deprovisioned: "gray" };

// What to send someone who was invited: this deployment sends no email itself.
export function inviteText(email: string, by: string, origin: string): string {
  return `${by} invited you to the meta-harness console. Sign in at ${origin} with your Google account, ${email}.`;
}

export function UsersPage() {
  const [users, setUsers] = useState<ConsoleUser[] | null>(null);
  const [me, setMe] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("user");
  const [busy, setBusy] = useState(false);
  // The user whose Deprovision was clicked once: a second click does it.
  const [confirming, setConfirming] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const load = useCallback(() => api.users().then(setUsers, (err) => setError(err instanceof Error ? err.message : String(err))), []);
  useEffect(() => {
    void load();
    api.me().then((m) => setMe(m.identity), () => setMe(""));
  }, [load]);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      setConfirming(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const admins = (users ?? []).filter((u) => u.role === "admin" && u.status !== "deprovisioned").length;
  const invite = () =>
    act(async () => {
      await api.inviteUser(email.trim(), role);
      setEmail("");
      setRole("user");
    });
  const copy = (u: ConsoleUser) => {
    void navigator.clipboard?.writeText(inviteText(u.email, u.invited_by, window.location.origin));
    setCopied(u.email);
  };

  return (
    <>
      <PageHeader title="Users" description="Who may sign in to the console. An admin changes configuration, uses the kill switch and opens the admin pages; a regular user does not." />
      <ErrorAlert error={error} />
      <Paper p="md" mb="lg" withBorder maw={720}>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void invite();
          }}
        >
          <Group gap="sm" align="flex-end">
            <TextInput label="Invite a user" placeholder="name@example.com" description="The Google account they sign in with." value={email} onChange={(e) => setEmail(e.currentTarget.value)} style={{ flex: 1 }} />
            <NativeSelect label="Role" data={ROLES} value={role} onChange={(e) => setRole(e.currentTarget.value)} />
            <Button type="submit" loading={busy} disabled={!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())} leftSection={<IconUserPlus size={16} />}>Invite</Button>
          </Group>
          <Text size="xs" c="dimmed" mt="xs">The console sends no email: after inviting, use Copy invite on their row and send it to them yourself.</Text>
        </form>
      </Paper>
      {users && users.length === 0 && <Empty>No users yet: the console's first access list still decides who may sign in.</Empty>}
      {users && users.length > 0 && (
        <DataTable head={["User", "Role", "Status", "Invited by", "Last sign-in", ""]}>
          {users.map((u) => {
            const gone = u.status === "deprovisioned";
            const lastAdmin = u.role === "admin" && !gone && admins === 1;
            return (
              <Table.Tr key={u.email} data-testid={`user-${u.email}`} opacity={gone ? 0.6 : 1}>
                <Table.Td>
                  <Text size="sm" fw={500}>{u.email}{u.email === me && <Text span size="xs" c="dimmed"> (you)</Text>}</Text>
                </Table.Td>
                <Table.Td>
                  {gone ? (
                    <Text size="sm" c="dimmed">{u.role === "admin" ? "Admin" : "Regular user"}</Text>
                  ) : (
                    <Stack gap={2}>
                      <NativeSelect
                        aria-label={`Role of ${u.email}`}
                        size="xs"
                        w={150}
                        data={ROLES}
                        value={u.role}
                        disabled={busy || lastAdmin}
                        onChange={(e) => void act(() => api.setUserRole(u.email, e.currentTarget.value))}
                      />
                      {lastAdmin && <Text size="xs" c="dimmed">The only admin: make someone else an admin first.</Text>}
                    </Stack>
                  )}
                </Table.Td>
                <Table.Td><Badge color={STATUS_COLOR[u.status] ?? "gray"} variant="light" tt="none">{u.status}</Badge></Table.Td>
                <Table.Td><Text size="sm" c="dimmed">{u.invited_by}, {when(u.invited_at)}</Text></Table.Td>
                <Table.Td><Text size="sm" c="dimmed">{u.last_sign_in_at ? when(u.last_sign_in_at) : "Never"}</Text></Table.Td>
                <Table.Td>
                  <Group gap="xs" justify="flex-end" wrap="nowrap">
                    {u.status === "invited" && (
                      <Button size="compact-xs" variant="light" onClick={() => copy(u)}>{copied === u.email ? "Copied" : "Copy invite"}</Button>
                    )}
                    {gone ? (
                      <Button size="compact-xs" variant="light" loading={busy} onClick={() => void act(() => api.inviteUser(u.email, u.role))}>Invite again</Button>
                    ) : confirming === u.email ? (
                      <Button size="compact-xs" color="red" loading={busy} onClick={() => void act(() => api.deprovisionUser(u.email))}>Confirm: end their access</Button>
                    ) : (
                      <Button size="compact-xs" variant="light" color="red" disabled={lastAdmin} onClick={() => setConfirming(u.email)}>
                        {u.status === "invited" ? "Remove" : "Deprovision"}
                      </Button>
                    )}
                  </Group>
                </Table.Td>
              </Table.Tr>
            );
          })}
        </DataTable>
      )}
    </>
  );
}
