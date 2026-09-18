import { defineConfig } from "@playwright/test";

const externalServers = Boolean(process.env.PLAYWRIGHT_EXTERNAL_SERVERS);

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  workers: 1,
  use: { baseURL: "http://localhost:3101", trace: "retain-on-failure" },
  webServer: externalServers
    ? undefined
    : {
        command: "docker compose --profile fixture up --build postgres redis api-fixture web-fixture",
        url: "http://localhost:3101",
        timeout: 180_000,
        reuseExistingServer: true,
      },
});
