import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 前端开发服务器：5173，/api 代理到 Flask 后端 8765
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
      },
    },
  },
});
