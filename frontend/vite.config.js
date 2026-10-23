import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/ride-api": {
        target: process.env.RIDE_API_TARGET || "http://127.0.0.1:8010",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/ride-api/, ""),
      },
    },
  },
});
