/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.VITE_BACKEND_URL ?? "http://127.0.0.1:18000";

// 오래 캐시되는 공용 조각: 배포마다 화면 코드가 바뀌어도 라이브러리 조각은 이름(해시)이 그대로라 다시 받지 않는다
const VENDOR = /[\\/]node_modules[\\/](react|react-dom|scheduler|react-router|react-router-dom|@tanstack[\\/][^\\/]+)[\\/]/;
// 차트 라이브러리는 차트를 그리는 화면(대시보드·종목·백테스트)에서만 받는다 (components/LazyCharts.tsx)
const CHARTS = /[\\/]node_modules[\\/](lightweight-charts|fancy-canvas)[\\/]/;

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (CHARTS.test(id)) return "vendor-charts";
          if (VENDOR.test(id)) return "vendor-react";
          return undefined;
        },
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 15173,
    strictPort: true,
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
