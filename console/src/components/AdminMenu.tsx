import type { ComponentType } from "react";
import { NavLink as RouterLink, useLocation } from "react-router-dom";
import { Menu, NavLink } from "@mantine/core";
import { IconChevronRight, IconShieldCog } from "@tabler/icons-react";

// The admin pages as one entry at the bottom of the left pane. A click opens them in a menu beside the pane, so
// they take one row of it whether open or not. The entry is highlighted while an admin page is showing.
export function AdminMenu(props: { items: { label: string; to: string; icon: ComponentType<{ size?: number; stroke?: number }> }[] }) {
  const path = useLocation().pathname;
  const showing = (to: string) => path === to || path.startsWith(to + "/");
  if (!props.items.length) return null;
  return (
    <Menu position="right-end" offset={12} width={220} withinPortal>
      <Menu.Target>
        <NavLink
          component="button"
          label="Admin"
          leftSection={<IconShieldCog size={18} stroke={1.6} />}
          rightSection={<IconChevronRight size={14} />}
          active={props.items.some((item) => showing(item.to))}
          variant="light"
          style={{ borderRadius: "var(--mantine-radius-md)" }}
        />
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>Admin</Menu.Label>
        {props.items.map((item) => (
          <Menu.Item
            key={item.to}
            component={RouterLink}
            to={item.to}
            leftSection={<item.icon size={16} stroke={1.6} />}
            color={showing(item.to) ? "indigo" : undefined}
            fw={showing(item.to) ? 600 : undefined}
          >
            {item.label}
          </Menu.Item>
        ))}
      </Menu.Dropdown>
    </Menu>
  );
}
