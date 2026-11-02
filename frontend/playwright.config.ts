import { defineConfig, devices } from "@playwright/test";

// E2E 는 개발 서버와 겹치지 않도록 별도 포트(8001/5174)와 임시 DB 로 띄운다
const BACKEND_PORT = 8001;
const FRONT_PORT = 5174;
const tmp = "test-results/e2e-data";

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${FRONT_PORT}`,
    trace: "retain-on-failure",
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
  ],
  webServer: [
    {
      command:
        `rm -rf ${tmp} && mkdir -p ${tmp} && cd ../backend && ` +
        `DATABASE_URL=sqlite:///../frontend/${tmp}/e2e.db DATA_DIR=../frontend/${tmp} ` +
        `ENABLE_SCHEDULER=false SIM_START=2026-11-02T10:00:00 DATA_SOURCE=mock LLM_BASE_URL= LLM_API_KEY= ` +
        `uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port ${BACKEND_PORT}`,
      url: `http://127.0.0.1:${BACKEND_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npx vite --host 127.0.0.1 --port ${FRONT_PORT} --strictPort`,
      url: `http://127.0.0.1:${FRONT_PORT}`,
      env: { VITE_BACKEND_URL: `http://127.0.0.1:${BACKEND_PORT}` },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
