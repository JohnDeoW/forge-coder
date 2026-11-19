import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 构建产物输出到 Python 包内，由 FastAPI 提供；开发时把 /api 代理到面板服务（forge-coder --web --port 8900）
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../forge_coder/ui/web/dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.FORGE_API ?? "http://127.0.0.1:8900", changeOrigin: true } },
  },
});
