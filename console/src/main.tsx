import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider, createBrowserRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import { App } from "./App";
import { theme } from "./theme";
import "./styles.css";

// A data router, so an editor can hold a navigation while it asks about unsaved changes (useBlocker).
const router = createBrowserRouter([{ path: "*", element: <App /> }]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="auto">
      <Notifications position="top-right" />
      <RouterProvider router={router} />
    </MantineProvider>
  </StrictMode>,
);
