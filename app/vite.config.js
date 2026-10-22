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
    // The backend (Python, its HTML templates) and the Rust crate are not part of the page: a change there must not reload it and lose the backend's one-time "ready" (Tauri rebuilds its own side)
    watch: { ignored: ["**/python_backend/**", "**/src-tauri/**", "**/premiere_extension/**", "**/dev/**", "**/tests/**"] },
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    target: "es2020",
    minify: "esbuild",
    sourcemap: false,
  },
});
