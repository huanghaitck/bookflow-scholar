import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: 'h3-review-evidence.spec.ts',
  timeout: 150_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  outputDir: 'test-results/h3-review',
  use: {
    baseURL: 'http://127.0.0.1:4318',
    channel: 'msedge',
    locale: 'zh-CN',
    colorScheme: 'light',
  },
  webServer: {
    command: 'corepack pnpm exec vite preview --host 127.0.0.1 --port 4318 --strictPort --outDir UI_HANDOFF_BUNDLE/build/app',
    url: 'http://127.0.0.1:4318/?mockScenario=translation_running',
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
