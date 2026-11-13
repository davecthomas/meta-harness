import { useCallback, useEffect, useState } from "react";
import { NavLink as RouterLink, Route, Routes, useLocation } from "react-router-dom";
import {
  ActionIcon,
  Alert,
  Anchor,
  AppShell,
  Avatar,
  Burger,
  Center,
  Group,
  Loader,
  Menu,
  NavLink,
  ScrollArea,
  Text,
  Title,
  Tooltip,
  UnstyledButton,
  useComputedColorScheme,
  useMantineColorScheme,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconHexagons, IconMoon, IconPlus, IconSettings, IconSun } from "@tabler/icons-react";
import { api, ApiError } from "./api";
import { SIGNED_OUT } from "./client";
import { SignIn, signOut } from "./features/auth/SignIn";
import { Inbox } from "./components/Inbox";
import { FEATURES } from "./features";
import { CommandPalette } from "./components/CommandPalette";
import { AdminMenu } from "./components/AdminMenu";
import { KillSwitchBanner, KillSwitchButton } from "./components/KillSwitch";
import { TreeItem } from "./components/NavTree";

// The navigation's entries, from the features, in order; admin entries only for an admin. The Admin section's
// entries go in one menu at the bottom of the pane.
// Entries with a history (Goals, Tasks) come after the plain links, so their long lists don't push the links down.
function navItems(admin: boolean, section: "Operate" | "Admin") {
  const items = FEATURES.flatMap((f) => f.nav).filter((n) => (admin || !n.admin) && n.section === section);
  return [...items.filter((n) => !n.history), ...items.filter((n) => n.history)];
}

function isActive(path: string, to: string): boolean {
  if (to === "/") return path === "/";
  return path === to || path.startsWith(to + "/");
}

export function App() {
  const [who, setWho] = useState<string | null>(null);
  const [admin, setAdmin] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const [signIn, setSignIn] = useState(false);
  // Why the sign-in screen shows, when it is not the first visit: the session ended while the console was open.
  const [expired, setExpired] = useState(false);
  const [opened, { toggle, close }] = useDisclosure();
  // The pane's lists that are closed: Goals and Tasks start open.
  const [shut, setShut] = useState<Record<string, boolean>>({});
  const location = useLocation();
  const { setColorScheme } = useMantineColorScheme();

  // A signed-in user's saved appearance applies wherever they sign in.
  useEffect(() => {
    if (!who) return;
    api.settings().then((s) => setColorScheme(s.appearance === "system" ? "auto" : s.appearance), () => undefined);
  }, [who, setColorScheme]);

  useEffect(() => {
    api.me().then(
      (me) => {
        setWho(me.identity);
        setAdmin((me.roles ?? []).includes("admin"));
      },
      (err) => {
        if (err instanceof ApiError && err.status === 401) setSignIn(true);
        else setRefused(err instanceof ApiError ? err.detail : String(err));
      },
    );
  }, []);
  // A request any page makes may find the session gone: sign in again, and come back to the same page.
  useEffect(() => {
    const ended = () => {
      setExpired(true);
      setSignIn(true);
    };
    window.addEventListener(SIGNED_OUT, ended);
    return () => window.removeEventListener(SIGNED_OUT, ended);
  }, []);
  const signedIn = useCallback((identity: string) => {
    setSignIn(false);
    setExpired(false);
    setWho(identity);
    api.me().then((me) => setAdmin((me.roles ?? []).includes("admin")), () => setAdmin(false));
  }, []);


  useEffect(close, [location.pathname, close]);

  // After every hook: React needs the same hooks on every render.
  if (signIn) return <SignIn onSignedIn={signedIn} expired={expired} />;

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 300, breakpoint: "sm", collapsed: { mobile: !opened } }} padding="lg">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Menu" />
            <IconHexagons size={26} stroke={1.6} color="var(--mantine-primary-color-filled)" />
            <Title order={4}>Meta-harness</Title>
            {who && <VersionTag />}
          </Group>
          <Group gap="xs" wrap="nowrap">
            {who && admin && <KillSwitchButton />}
            {who && <CommandPalette admin={admin} />}
            {who && <Inbox />}
            <ColorSchemeToggle />
            {who && <UserMenu who={who} />}
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <AppShell.Section grow component={ScrollArea}>
          {navItems(admin, "Operate").map((item) =>
            item.history && who && !refused ? (
              <TreeItem
                key={item.to}
                label={item.label}
                to={item.to}
                active={location.pathname === item.to}
                leftSection={<item.icon size={18} stroke={1.6} />}
                opened={!shut[item.to]}
                onToggle={() => setShut({ ...shut, [item.to]: !shut[item.to] })}
                bold
              >
                <item.history />
              </TreeItem>
            ) : (
              <NavLink
                key={item.to}
                component={RouterLink}
                to={item.to}
                label={item.label}
                leftSection={<item.icon size={18} stroke={1.6} />}
                active={isActive(location.pathname, item.to)}
                variant="light"
                style={{ borderRadius: "var(--mantine-radius-md)" }}
              />
            ),
          )}
        </AppShell.Section>
        <AppShell.Section pt="xs">
          <NavLink
            component={RouterLink}
            to="/goals/new"
            label="New goal"
            leftSection={<IconPlus size={18} stroke={1.6} />}
            variant="filled"
            active
            style={{ borderRadius: "var(--mantine-radius-md)" }}
          />
          <AdminMenu items={navItems(admin, "Admin")} />
        </AppShell.Section>
      </AppShell.Navbar>

      <AppShell.Main>
        {refused ? (
          <Alert color="red" title="Not allowed" maw={640}>{refused}</Alert>
        ) : who === null ? (
          <Center h={240}><Loader /></Center>
        ) : (
          <>
          <KillSwitchBanner admin={admin} />
          <Routes>
            {FEATURES.flatMap((f) => f.routes).map((r) => (
              <Route key={r.path} path={r.path} element={r.element} />
            ))}
          </Routes>
          </>
        )}
      </AppShell.Main>
    </AppShell>
  );
}

function ColorSchemeToggle() {
  const { setColorScheme } = useMantineColorScheme();
  const dark = useComputedColorScheme("light", { getInitialValueInEffect: true }) === "dark";
  return (
    <Tooltip label={dark ? "Light theme" : "Dark theme"}>
      <ActionIcon variant="default" size="lg" onClick={() => setColorScheme(dark ? "light" : "dark")} aria-label="Toggle theme">
        {dark ? <IconSun size={18} stroke={1.6} /> : <IconMoon size={18} stroke={1.6} />}
      </ActionIcon>
    </Tooltip>
  );
}

function UserMenu({ who }: { who: string }) {
  return (
    <Menu position="bottom-end" width={280}>
      <Menu.Target>
        <UnstyledButton aria-label="Signed-in operator">
          <Avatar name={who} color="initials" radius="xl" size={34} />
        </UnstyledButton>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>Signed in with Google</Menu.Label>
        <Menu.Item disabled>{who}</Menu.Item>
        <Menu.Divider />
        <Menu.Item component={RouterLink} to="/settings" leftSection={<IconSettings size={16} />}>Settings</Menu.Item>
        <Menu.Item onClick={() => void signOut()}>Sign out</Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}

// The version and the deployed commit, small and dimmed after the title. The commit links to it on GitHub.
function VersionTag() {
  const [running, setRunning] = useState<{ version: string; commit: string | null } | null>(null);
  useEffect(() => {
    api.version().then(setRunning, () => setRunning(null));
  }, []);
  if (!running) return null;
  return (
    <Text size="xs" c="dimmed" visibleFrom="xs" data-testid="version">
      v{running.version}
      {running.commit && (
        <>
          {" · "}
          <Tooltip label={running.commit}>
            <Anchor href={`https://github.com/davecthomas/meta-harness/commit/${running.commit}`} target="_blank" c="dimmed" size="xs" ff="monospace">
              {running.commit.slice(0, 7)}
            </Anchor>
          </Tooltip>
        </>
      )}
    </Text>
  );
}
