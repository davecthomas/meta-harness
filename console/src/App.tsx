import { Fragment, useCallback, useEffect, useState } from "react";
import { useWorkStream } from "./features/goals/goalSync";
import { NavLink as RouterLink, Route, Routes, useLocation } from "react-router-dom";
import {
  ActionIcon,
  Alert,
  Anchor,
  AppShell,
  Avatar,
  Box,
  Burger,
  Button,
  Center,
  Group,
  Image,
  Loader,
  Menu,
  NavLink,
  ScrollArea,
  Text,
  Tooltip,
  UnstyledButton,
  useComputedColorScheme,
  useMantineColorScheme,
} from "@mantine/core";
import { useDisclosure, useHotkeys } from "@mantine/hooks";
import { IconLayoutSidebarLeftCollapse, IconLayoutSidebarLeftExpand, IconMoon, IconPlus, IconSettings, IconSun } from "@tabler/icons-react";
import { api, ApiError } from "./api";
import { SIGNED_OUT } from "./client";
import { SignIn, signOut } from "./features/auth/SignIn";
import { Inbox } from "./components/Inbox";
import { FEATURES } from "./features";
import { CommandPalette } from "./components/CommandPalette";
import { AdminMenu } from "./components/AdminMenu";
import { AdminProvider } from "./components/Role";
import { loadTrackers } from "./features/trackers/refs";
import { KillSwitchBanner, KillSwitchButton } from "./components/KillSwitch";
import { TreeItem } from "./components/NavTree";
import { NewVersion } from "./components/NewVersion";
import { Footer } from "./components/Footer";
import lockup from "./brand/muxxy-lockup.svg";
import symbol from "./brand/muxxy-symbol.svg";
import { LOGO_HEIGHT } from "./brand/sizes";

// The navigation's entries, from the features, in order; admin entries only for an admin. The Admin section's
// entries go in one menu at the bottom of the pane.
// Entries with a history (Goals, Tasks) come after the plain links, so their long lists don't push the links down.
// New goal, which sits just above Goals in the pane.
const NEW_GOAL = (
  <NavLink
    component={RouterLink}
    to="/goals/new"
    label="New goal"
    leftSection={<IconPlus size={18} stroke={1.6} />}
    variant="filled"
    active
    mb={4}
    style={{ borderRadius: "var(--mantine-radius-md)" }}
  />
);

function navItems(admin: boolean, section: "Operate" | "Admin" | "Help") {
  const items = FEATURES.flatMap((f) => f.nav).filter((n) => (admin || !n.admin) && n.section === section);
  const placed = items.filter((n) => !n.last);
  return [...placed.filter((n) => !n.history), ...placed.filter((n) => n.history), ...items.filter((n) => n.last)];
}

function isActive(path: string, to: string): boolean {
  if (to === "/") return path === "/";
  return path === to || path.startsWith(to + "/");
}

// The header's height, and the logo's in it. At 53 pixels the lockup's name has letters about 22 pixels tall,
// which read; at the 32 it once had, on artwork with empty margins, they were 7.
export const HEADER_HEIGHT = 72;
export { LOGO_HEIGHT };
const MENU_HIDDEN = "mh:menu-hidden";

// Whether the menu was hidden last time. Storage that cannot be read leaves it shown.
function readHidden(): boolean {
  try {
    return window.localStorage.getItem(MENU_HIDDEN) === "1";
  } catch {
    return false;
  }
}

function keepHidden(hidden: boolean): boolean {
  try {
    window.localStorage.setItem(MENU_HIDDEN, hidden ? "1" : "0");
  } catch {
    // Not kept: the menu still hides or shows for this visit.
  }
  return hidden;
}

// The button that hides or shows the left menu on a wide screen. A phone has the burger instead.
function MenuToggle({ hidden, onToggle }: { hidden: boolean; onToggle: () => void }) {
  const label = hidden ? "Show the menu" : "Hide the menu";
  return (
    <Tooltip label={`${label} (⌘\\ or Ctrl+\\)`} withArrow>
      <ActionIcon variant="subtle" color="gray" size="lg" onClick={onToggle} aria-label={label} visibleFrom="sm">
        {hidden ? <IconLayoutSidebarLeftExpand size={20} /> : <IconLayoutSidebarLeftCollapse size={20} />}
      </ActionIcon>
    </Tooltip>
  );
}

export function App() {
  const [who, setWho] = useState<string | null>(null);
  const [admin, setAdmin] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const [notAllowed, setNotAllowed] = useState(false);
  const [signIn, setSignIn] = useState(false);
  // Why the sign-in screen shows, when it is not the first visit: the session ended while the console was open.
  const [expired, setExpired] = useState(false);
  const [opened, { toggle, close }] = useDisclosure();
  // On a wide screen the menu can be hidden, for more room on the page; the choice is kept for next time.
  const [hidden, setHidden] = useState(() => readHidden());
  const toggleMenu = useCallback(() => setHidden((was) => keepHidden(!was)), []);
  // The key by its place on the keyboard, so the shortcut works whatever character a layout puts there.
  useHotkeys([["mod+backslash", toggleMenu, { usePhysicalKeys: true }]]);
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
      async (me) => {
        // The trackers first, so every page can link an issue whichever tracker it is in.
        await loadTrackers();
        setWho(me.identity);
        setAdmin((me.roles ?? []).includes("admin"));
      },
      (err) => {
        if (err instanceof ApiError && err.status === 401) setSignIn(true);
        else {
          // Refused (403) is not the same as unreachable: each says what to do.
          setNotAllowed(err instanceof ApiError && err.status === 403);
          setRefused(err instanceof ApiError ? err.detail : String(err));
        }
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
    void loadTrackers();
    api.me().then((me) => setAdmin((me.roles ?? []).includes("admin")), () => setAdmin(false));
  }, []);


  useEffect(close, [location.pathname, close]);
  // One stream of everything that changes the work, for every page, while someone is signed in.
  useWorkStream(who !== null);

  // After every hook: React needs the same hooks on every render.
  if (signIn) return <SignIn onSignedIn={signedIn} expired={expired} />;

  return (
    <AppShell header={{ height: HEADER_HEIGHT }} navbar={{ width: 300, breakpoint: "sm", collapsed: { mobile: !opened, desktop: hidden } }} padding="lg">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Menu" />
            {who && <MenuToggle hidden={hidden} onToggle={toggleMenu} />}
            <Anchor component={RouterLink} to="/" aria-label="Muxxy" display="flex" style={{ lineHeight: 0 }}>
              <Image src={lockup} alt="Muxxy" h={LOGO_HEIGHT} w="auto" fit="contain" visibleFrom="sm" />
              <Image src={symbol} alt="Muxxy" h={LOGO_HEIGHT} w="auto" fit="contain" hiddenFrom="sm" />
            </Anchor>
          </Group>
          <Group gap="xs" wrap="nowrap">
            {who && <NewVersion />}
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
          {navItems(admin, "Operate").map((item) => (
            <Fragment key={item.to}>
              {item.to === "/goals" && NEW_GOAL}
              {item.history && who && !refused ? (
                <TreeItem
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
                  component={RouterLink}
                  to={item.to}
                  label={item.label}
                  leftSection={<item.icon size={18} stroke={1.6} />}
                  rightSection={item.badge && who && !refused ? <item.badge /> : undefined}
                  active={isActive(location.pathname, item.to)}
                  variant="light"
                  style={{ borderRadius: "var(--mantine-radius-md)" }}
                />
              )}
            </Fragment>
          ))}
        </AppShell.Section>
        <AppShell.Section pt="xs">
          {navItems(admin, "Help").map((item) => (
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
          ))}
          <AdminMenu items={navItems(admin, "Admin")} />
        </AppShell.Section>
      </AppShell.Navbar>

      {/* A column, so the footer sits at the foot of a short page and after the content of a long one. */}
      <AppShell.Main style={{ display: "flex", flexDirection: "column" }}>
        <Box style={{ flex: 1 }}>
        {refused ? (
          <Alert color="red" title={notAllowed ? "This account may not open the console" : "The console could not be reached"} maw={640} data-testid="refused">
            <Text size="sm" mb="sm">{refused}</Text>
            {notAllowed ? (
              <Group gap="sm">
                <Text size="sm">Ask an admin to invite this Google account, or sign in with an invited one.</Text>
                <Button size="compact-sm" variant="white" color="red" onClick={() => void signOut()}>Sign out and use another account</Button>
              </Group>
            ) : (
              <Group gap="sm">
                <Text size="sm">Your account is fine. The console may be restarting for a deploy.</Text>
                <Button size="compact-sm" variant="white" color="red" onClick={() => window.location.reload()}>Try again</Button>
              </Group>
            )}
          </Alert>
        ) : who === null ? (
          <Center h={240}><Loader /></Center>
        ) : (
          <AdminProvider value={admin}>
          <KillSwitchBanner admin={admin} />
          <Routes>
            {FEATURES.flatMap((f) => f.routes).map((r) => (
              <Route key={r.path} path={r.path} element={r.element} />
            ))}
            <Route path="*" element={<NoSuchPage />} />
          </Routes>
          </AdminProvider>
        )}
        </Box>
        <Footer version={who !== null} />
      </AppShell.Main>
    </AppShell>
  );
}

// An address that is no page's: a mistyped or old link says so, with the ways on, where it once showed nothing.
function NoSuchPage() {
  const location = useLocation();
  return (
    <Alert color="yellow" title="No such page" maw={640} data-testid="no-such-page">
      <Text size="sm" mb="xs">
        Nothing is at <Text span ff="monospace">{location.pathname}</Text>. The link may be old, or the address mistyped.
      </Text>
      <Group gap="md">
        <Anchor component={RouterLink} to="/" size="sm">Overview</Anchor>
        <Anchor component={RouterLink} to="/goals" size="sm">Goals</Anchor>
        <Anchor component={RouterLink} to="/tasks" size="sm">Tasks</Anchor>
      </Group>
    </Alert>
  );
}

// The theme, changed here or under Settings, Appearance: both keep it in the person's settings, so it is the
// same on the next visit and on another machine.
function ColorSchemeToggle() {
  const { setColorScheme } = useMantineColorScheme();
  const dark = useComputedColorScheme("light", { getInitialValueInEffect: true }) === "dark";
  function choose(next: "light" | "dark") {
    setColorScheme(next);
    // Saved on what the settings hold now; a failed save leaves the theme changed for this visit only.
    api.settings().then((now) => api.saveSettings({ ...now, appearance: next }), () => undefined).catch(() => undefined);
  }
  return (
    <Tooltip label={dark ? "Light theme" : "Dark theme"}>
      <ActionIcon variant="default" size="lg" onClick={() => choose(dark ? "light" : "dark")} aria-label="Toggle theme">
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
