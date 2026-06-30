import { defineConfig } from 'vitest/config';
import path from 'node:path';

export default defineConfig({
  test: {
    environment: 'node',
    globals: true,
    include: ['tests/**/*.test.ts'],
    // DB-backed integration/tenancy tests run serially against one Postgres db.
    fileParallelism: false,
    setupFiles: ['tests/setup-env.ts'],
    globalSetup: ['tests/global-setup.ts'],
    testTimeout: 30000,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
      // `server-only`/`client-only` are RSC runtime guards with no behavior to
      // test; stub them so server modules can be unit-tested under node.
      'server-only': path.resolve(__dirname, 'tests/stubs/empty.ts'),
      'client-only': path.resolve(__dirname, 'tests/stubs/empty.ts'),
    },
  },
});
