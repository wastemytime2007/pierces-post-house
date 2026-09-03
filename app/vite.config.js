import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri needs a fixed port and a stable dev server URL
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: "localhost",
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    target: "es2020",
    minify: "esbuild",
    sourcemap: false,
  },
});
