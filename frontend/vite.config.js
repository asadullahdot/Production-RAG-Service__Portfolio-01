import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The build goes straight into app/static/, which FastAPI already serves (index at "/", assets under "/static/").
// `npm run dev` serves the UI on :5173/static/ and forwards API calls to a locally running `uvicorn app.main:app`.
const api = "http://127.0.0.1:8000";
export default defineConfig({
  base: "/static/",
  plugins: [react(), tailwindcss()],
  build: { outDir: "../app/static", emptyOutDir: true },
  server: { proxy: { "/documents": api, "/ask": api, "/auth": api, "/health": api, "/metrics": api } },
  test: { environment: "node" },
});
