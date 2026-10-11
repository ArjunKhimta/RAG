import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import {
  API_PROXY_TARGET,
  BUILT_SITE_HEADERS,
  SHARED_SECURITY_HEADERS,
} from "./securityHeaders.js";

const SITE_HOST = "localhost";
const SITE_PORT = 5173;

const apiProxy = {
  "/api": {
    target: API_PROXY_TARGET,
    changeOrigin: false,
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: SITE_HOST,
    port: SITE_PORT,
    strictPort: true,
    proxy: apiProxy,
    headers: SHARED_SECURITY_HEADERS,
  },
  preview: {
    host: SITE_HOST,
    port: SITE_PORT,
    strictPort: true,
    proxy: apiProxy,
    headers: BUILT_SITE_HEADERS,
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{js,jsx}"],
    setupFiles: ["tests/setup.js"],
  },
});
