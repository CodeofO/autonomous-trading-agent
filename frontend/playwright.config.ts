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
      // 테스트 전용 서버: 가짜 토스 + 고정 시각, 실제 LLM·토스 호출 없음
      command:
        `rm -rf ${tmp} && mkdir -p ${tmp} && cd ../backend && ` +
        `DATABASE_URL=sqlite:///../frontend/${tmp}/e2e.db DATA_DIR=../frontend/${tmp} ` +
        `E2E_PORT=${BACKEND_PORT} uv run python -m tests.e2e_server`,
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
