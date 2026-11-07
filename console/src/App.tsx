import { useEffect, useState } from "react";
import { NavLink as RouterLink, Route, Routes, useLocation } from "react-router-dom";
import {
  ActionIcon,
  Alert,
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
  IconHexagons,
  IconLayoutDashboard,
  IconListCheck,
  IconMoon,
  IconPlus,
  IconSun,
  IconTarget,
} from "@tabler/icons-react";
import { api, ApiError } from "./api";
import { Overview } from "./pages/Overview";
import { NewGoal } from "./pages/NewGoal";
import { GoalPage } from "./pages/GoalPage";
import { GoalsPage } from "./pages/GoalsPage";
import { TasksPage } from "./pages/TasksPage";
import { TaskPage } from "./pages/TaskPage";

const NAV = [
  {
    section: "Operate",
    items: [
      { to: "/", label: "Overview", icon: IconLayoutDashboard },
      { to: "/goals", label: "Goals", icon: IconTarget },
      { to: "/tasks", label: "Tasks", icon: IconListCheck },
    ],
  },
];

function isActive(path: string, to: string): boolean {
  if (to === "/") return path === "/";
  if (to === "/goals") return path === "/goals" || (path.startsWith("/goals/") && path !== "/goals/new");
  return path === to || path.startsWith(to + "/");
}

export function App() {
  const [who, setWho] = useState<string | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  const [opened, { toggle, close }] = useDisclosure();
  const location = useLocation();

  useEffect(() => {
    api.me().then(
      (me) => setWho(me.identity),
      (err) => setRefused(err instanceof ApiError ? err.detail : String(err)),
    );
  }, []);

  useEffect(close, [location.pathname, close]);

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 240, breakpoint: "sm", collapsed: { mobile: !opened } }} padding="lg">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Menu" />
            <IconHexagons size={26} stroke={1.6} color="var(--mantine-primary-color-filled)" />
            <Title order={4}>Meta-harness</Title>
          </Group>
          <Group gap="xs" wrap="nowrap">
            <ColorSchemeToggle />
            {who && <UserMenu who={who} />}
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <AppShell.Section grow component={ScrollArea}>
          {NAV.map((group) => (
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
            <Route path="/tasks/:taskId" element={<TaskPage />} />
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
        <Menu.Label>Signed in through Identity-Aware Proxy</Menu.Label>
        <Menu.Item disabled>{who}</Menu.Item>
        <Menu.Divider />
        <Menu.Item component="a" href="/?gcp-iap-mode=CLEAR_LOGIN_COOKIE">Sign out</Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}
