import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// `npm run dev` serves the app with hot reload and sends /v1 to the local edge (`make serve`),
// which adds the operator's identity and routes each path to its owning service.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/v1": { target: "http://127.0.0.1:8300", changeOrigin: false } },
  },
  build: { outDir: "dist", sourcemap: true },
  test: { environment: "jsdom", globals: true },
});
