import { useEffect, useRef, useState } from "react";
import { Alert, Center, Loader, Paper, Stack, Text, Title } from "@mantine/core";
import { IconHexagons } from "@tabler/icons-react";
import { api, ApiError } from "../api";

// Google Identity Services: Google's sign-in button, which hands this page an ID token for the console's
// OAuth client. The edge verifies the token and sets the session (src/meta_harness/edge/app.py).
const GIS_SCRIPT = "https://accounts.google.com/gsi/client";

interface GoogleId {
  initialize(options: { client_id: string; callback: (r: { credential: string }) => void; ux_mode?: string }): void;
  renderButton(element: HTMLElement, options: Record<string, unknown>): void;
  disableAutoSelect(): void;
}

declare global {
  interface Window {
    google?: { accounts: { id: GoogleId } };
  }
}

function loadGoogle(): Promise<GoogleId> {
  if (window.google) return Promise.resolve(window.google.accounts.id);
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = GIS_SCRIPT;
    script.async = true;
    script.onload = () => (window.google ? resolve(window.google.accounts.id) : reject(new Error("Google sign-in did not load")));
    script.onerror = () => reject(new Error("Google sign-in did not load"));
    document.head.appendChild(script);
  });
}

export function SignIn({ onSignedIn }: { onSignedIn: (identity: string) => void }) {
  const button = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .authConfig()
      .then(async ({ google_client_id }) => {
        if (!google_client_id) throw new Error("Google sign-in is not configured for this console.");
        const google = await loadGoogle();
        if (!live || !button.current) return;
        google.initialize({
          client_id: google_client_id,
          callback: ({ credential }) =>
            api.signIn(credential).then(
              (me) => onSignedIn(me.identity),
              (err) => setError(err instanceof ApiError ? err.detail : String(err)),
            ),
        });
        google.renderButton(button.current, { theme: "outline", size: "large", text: "signin_with", shape: "pill" });
        setReady(true);
      })
      .catch((err) => live && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      live = false;
    };
  }, [onSignedIn]);

  return (
    <Center h="70vh">
      <Paper withBorder radius="lg" p="xl" w={380}>
        <Stack align="center" gap="md">
          <IconHexagons size={40} stroke={1.5} color="var(--mantine-primary-color-filled)" />
          <Title order={3}>Meta-harness</Title>
          <Text c="dimmed" size="sm" ta="center">
            Sign in with the Google account on the console's access list.
          </Text>
          {!ready && !error && <Loader size="sm" />}
          <div ref={button} data-testid="google-sign-in" />
          {error && (
            <Alert color="red" title="Not signed in" w="100%">
              {error}
            </Alert>
          )}
        </Stack>
      </Paper>
    </Center>
  );
}

export function signOut() {
  window.google?.accounts.id.disableAutoSelect();
  return api.signOut().finally(() => window.location.assign("/"));
}
