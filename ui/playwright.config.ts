import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  fullyParallel: false,
  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
  ],
  outputDir: 'test-results',
  use: {
    baseURL: 'http://127.0.0.1:4317',
    channel: 'msedge',
    locale: 'zh-CN',
    colorScheme: 'light',
  },
  webServer: {
    command: 'corepack pnpm dev --host 127.0.0.1 --port 4317',
    url: 'http://127.0.0.1:4317',
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
