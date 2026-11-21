import { IconHelpCircle } from "@tabler/icons-react";
import { HelpPage } from "./HelpPage";
import { HelpTopics } from "./HelpTopics";
import type { Feature } from "../types";

export const feature: Feature = {
  routes: [
    { path: "/help", element: <HelpPage /> },
    { path: "/help/:slug", element: <HelpPage /> },
  ],
  nav: [{ section: "Operate", label: "Help", to: "/help", icon: IconHelpCircle, history: HelpTopics }],
};
