import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 45000,
  workers: 1,
  webServer: process.env.E2E_BASE_URL ? undefined : {
    command: process.platform === 'win32'
      ? '..\\..\\.venv\\Scripts\\python.exe ../../scripts/e2e_server.py'
      : '../../.venv/bin/python ../../scripts/e2e_server.py',
    url: 'http://127.0.0.1:8011/api/ready',
    reuseExistingServer: false,
    timeout: 60000,
  },
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:8011',
    headless: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
})

