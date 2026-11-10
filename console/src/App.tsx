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
import {
  IconAdjustments,
  IconBrandGithub,
  IconCloud,
  IconFileText,
  IconHexagons,
  IconHistory,
  IconPlayerStop,
  IconRocket,
  IconLayoutDashboard,
  IconGridDots,
  IconListCheck,
  IconMoon,
  IconPlus,
  IconSettings,
  IconSun,
  IconTarget,
} from "@tabler/icons-react";
import { api, ApiError } from "./api";
import { SignIn, signOut } from "./components/SignIn";
import { Overview } from "./pages/Overview";
import { NewGoal } from "./pages/NewGoal";
import { GoalPage } from "./pages/GoalPage";
import { GoalsPage } from "./pages/GoalsPage";
import { TasksPage } from "./pages/TasksPage";
import { ActivityPage } from "./pages/ActivityPage";
import { TaskPage } from "./pages/TaskPage";
import { AuditPage, CloudPage, DeploysPage, KillSwitchPage } from "./pages/AdminPages";
import { LogsPage } from "./pages/LogsPage";
import { ReposPage } from "./pages/ReposPage";
import { SettingsPage } from "./pages/SettingsPage";
import { Inbox } from "./components/Inbox";
import { ConfigPage } from "./pages/ConfigPage";

const NAV = [
  {
    section: "Operate",
    items: [
      { to: "/", label: "Overview", icon: IconLayoutDashboard },
      { to: "/goals", label: "Goals", icon: IconTarget },
      { to: "/tasks", label: "Tasks", icon: IconListCheck },
      { to: "/activity", label: "Activity", icon: IconGridDots },
    ],
  },
];

// Only admins see it: what changes how the meta-harness runs, and where it is deployed.
const ADMIN_NAV = {
  section: "Admin",
  items: [
    { to: "/config", label: "Configuration", icon: IconAdjustments },
    { to: "/admin/repos", label: "Repositories", icon: IconBrandGithub },
    { to: "/admin/kill-switch", label: "Kill switch", icon: IconPlayerStop },
    { to: "/admin/deploys", label: "Deploys", icon: IconRocket },
    { to: "/admin/logs", label: "Logs", icon: IconFileText },
    { to: "/admin/audit", label: "Audit", icon: IconHistory },
    { to: "/admin/cloud", label: "Google Cloud", icon: IconCloud },
  ],
};

function isActive(path: string, to: string): boolean {
  if (to === "/") return path === "/";
  if (to === "/goals") return path === "/goals" || (path.startsWith("/goals/") && path !== "/goals/new");
  return path === to || path.startsWith(to + "/");
}

export function App() {
  const [who, setWho] = useState<string | null>(null);
  const [admin, setAdmin] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const [signIn, setSignIn] = useState(false);
  const [opened, { toggle, close }] = useDisclosure();
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
  const signedIn = useCallback((identity: string) => {
    setSignIn(false);
    setWho(identity);
    api.me().then((me) => setAdmin((me.roles ?? []).includes("admin")), () => setAdmin(false));
  }, []);


  useEffect(close, [location.pathname, close]);

  // After every hook: React needs the same hooks on every render.
  if (signIn) return <SignIn onSignedIn={signedIn} />;

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 240, breakpoint: "sm", collapsed: { mobile: !opened } }} padding="lg">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Menu" />
            <IconHexagons size={26} stroke={1.6} color="var(--mantine-primary-color-filled)" />
            <Title order={4}>Meta-harness</Title>
            {who && <VersionTag />}
          </Group>
          <Group gap="xs" wrap="nowrap">
            {who && <Inbox />}
            <ColorSchemeToggle />
            {who && <UserMenu who={who} />}
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <AppShell.Section grow component={ScrollArea}>
          {(admin ? [...NAV, ADMIN_NAV] : NAV).map((group) => (
            <div key={group.section}>
              <Text size="xs" fw={600} c="dimmed" tt="uppercase" px="sm" mt="md" mb={6}>
                {group.section}
              </Text>
              {group.items.map((item) => (
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
            </div>
          ))}
        </AppShell.Section>
        <AppShell.Section>
          <NavLink
            component={RouterLink}
            to="/goals/new"
            label="New goal"
            leftSection={<IconPlus size={18} stroke={1.6} />}
            variant="filled"
            active
            style={{ borderRadius: "var(--mantine-radius-md)" }}
          />
        </AppShell.Section>
      </AppShell.Navbar>

      <AppShell.Main>
        {refused ? (
          <Alert color="red" title="Not allowed" maw={640}>{refused}</Alert>
        ) : who === null ? (
          <Center h={240}><Loader /></Center>
        ) : (
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/goals" element={<GoalsPage />} />
            <Route path="/goals/new" element={<NewGoal />} />
            <Route path="/goals/:goalId" element={<GoalPage />} />
            <Route path="/tasks" element={<TasksPage />} />
            <Route path="/activity" element={<ActivityPage />} />
            <Route path="/tasks/:taskId" element={<TaskPage />} />
            <Route path="/config" element={<ConfigPage />} />
            <Route path="/admin/kill-switch" element={<KillSwitchPage />} />
            <Route path="/admin/deploys" element={<DeploysPage />} />
            <Route path="/admin/logs" element={<LogsPage />} />
            <Route path="/admin/repos" element={<ReposPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/admin/audit" element={<AuditPage />} />
            <Route path="/admin/cloud" element={<CloudPage />} />
          </Routes>
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
