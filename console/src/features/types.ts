import type { ComponentType, ReactElement } from "react";

// What a feature adds to the console: its routes, and its entries in the navigation.
export interface Feature {
  routes: { path: string; element: ReactElement }[];
  // section: the navigation group. admin: shown only to admins.
  nav: { section: "Operate" | "Admin"; label: string; to: string; icon: ComponentType<{ size?: number; stroke?: number }>; admin?: boolean }[];
}
