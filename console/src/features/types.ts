import type { ComponentType, ReactElement } from "react";

// What a feature adds to the console: its routes, and its entries in the navigation.
export interface Feature {
  routes: { path: string; element: ReactElement }[];
  // section: the navigation group; the Admin entries share one menu at the bottom of the pane. admin: shown only
  // to admins. history: what the entry opens to in the pane, such as every goal, current and past.
  nav: {
    section: "Operate" | "Admin";
    label: string;
    to: string;
    icon: ComponentType<{ size?: number; stroke?: number }>;
    admin?: boolean;
    history?: ComponentType;
  }[];
}
