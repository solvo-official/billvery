import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type ProxyOptions } from "vite";

export default defineConfig(({ mode }) => {
  // Read without the VITE_ prefix filter: these values stay in this Node process and are never
  // compiled into browser code. The browser calls same-origin /api; the proxy adds the key.
  const env = loadEnv(mode, fileURLToPath(new URL(".", import.meta.url)), "");
  // 127.0.0.1 rather than localhost: uvicorn binds IPv4 only, and localhost can resolve to ::1 first.
  const target = env.AUDITOR_API_URL || "http://127.0.0.1:8000";
  const apiKey = env.AUDITOR_API_KEY;
  if (!apiKey) console.warn("[invoice-auditor] AUDITOR_API_KEY is not set in .env.local: API calls will be rejected (401).");

  const api: ProxyOptions = { target, changeOrigin: true, ...(apiKey ? { headers: { Authorization: `Bearer ${apiKey}` } } : {}) };
  const proxy = { "/api": api, "/healthz": { target, changeOrigin: true } };

  return {
    // Served from the domain root (Vercel serves frontend/dist there); absolute asset URLs keep
    // working when the SPA fallback rewrites a deep link to /index.html.
    base: "/",
    plugins: [react(), tailwindcss()],
    resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
    server: { port: 5173, strictPort: true, proxy },
    preview: { port: 4173, strictPort: true, proxy },
    // React 19 + the Radix primitives are ~160 KB gzipped; the app itself is small.
    build: { chunkSizeWarningLimit: 650 },
  };
});
