import { defineConfig } from "vite";

declare const process: {
  env: Record<string, string | undefined>;
};

export default defineConfig({
  server: {
    host: "0.0.0.0",
    proxy: {
      "/geoserver": {
        target: process.env.GEOSERVER_PROXY_TARGET ?? "http://127.0.0.1:8600",
        changeOrigin: true,
      },
    },
  },
});
