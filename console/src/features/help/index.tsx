import { IconHelpCircle } from "@tabler/icons-react";
import { HelpPage, OldTopic } from "./HelpPage";
import type { Feature } from "../types";

export const feature: Feature = {
  routes: [
    { path: "/help", element: <HelpPage /> },
    { path: "/help/:slug", element: <OldTopic /> },
  ],
  nav: [{ section: "Help", label: "Help", to: "/help", icon: IconHelpCircle }],
};
