import { NavLink, Text } from "@mantine/core";
import { NavLink as RouterLink, useLocation } from "react-router-dom";
import { TOPICS } from "./topics";

// Help's topics, under Help in the left pane, in reading order.
export function HelpTopics() {
  const path = useLocation().pathname;
  return (
    <>
      {TOPICS.map((t, n) => {
        const to = `/help/${t.slug}`;
        return (
          <NavLink
            key={t.slug}
            component={RouterLink}
            to={to}
            label={<Text size="sm" truncate>{t.title}</Text>}
            active={path === to || (n === 0 && path === "/help")}
            variant="light"
            style={{ borderRadius: "var(--mantine-radius-md)" }}
          />
        );
      })}
    </>
  );
}
