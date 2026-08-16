import { defineConfig, devices } from "@playwright/test";

const TEST_API_PORT = process.env.PLAYWRIGHT_API_PORT ?? "18100";
const TEST_API_BASE_URL = `http://127.0.0.1:${TEST_API_PORT}`;
const WEB_BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:3100";
const SKIP_WEB_SERVER = process.env.PLAYWRIGHT_SKIP_WEB_SERVER === "1";

export default defineConfig({
  testDir: "./tests",
  globalSetup: "./tests/global-setup.ts",
  timeout: 30_000,
  use: {
    baseURL: WEB_BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure"
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } }
  ],
  webServer: SKIP_WEB_SERVER
    ? undefined
    : {
        command: "pnpm dev --hostname 127.0.0.1 --port 3100",
        env: {
          API_BASE_URL: TEST_API_BASE_URL,
          PLAYWRIGHT_API_PORT: TEST_API_PORT
        },
        url: WEB_BASE_URL,
        reuseExistingServer: true,
        timeout: 120_000
      }
});
