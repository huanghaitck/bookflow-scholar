import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 120_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  outputDir: 'test-results/s3',
  use: {
    baseURL: 'http://127.0.0.1:4318',
    locale: 'zh-CN',
    colorScheme: 'light',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'corepack pnpm exec vite preview --host 127.0.0.1 --port 4318',
    url: 'http://127.0.0.1:4318/mascot-runtime.html',
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
