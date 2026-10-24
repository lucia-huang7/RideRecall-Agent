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
      "/api/python": {
        target: process.env.VITE_PYTHON_API_URL || "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/python/, ""),
      },
      "/api/java": {
        target: process.env.VITE_JAVA_API_URL || "http://localhost:8080",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/java/, ""),
      },
    },
  },
});
