import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  use: { baseURL: "http://localhost:3101", trace: "retain-on-failure" },
  webServer: {
    command: "docker compose --profile fixture up --build -d",
    url: "http://localhost:3101",
    timeout: 180_000,
    reuseExistingServer: true,
  },
});
