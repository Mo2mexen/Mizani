import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API has no CORS: in development, /api goes through this proxy to the backend.
// In Docker the API serves the built page itself, so both are on one origin.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8090" } },
});
