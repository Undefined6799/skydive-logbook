// Dedicated vitest config — TEST-8 (audit 2026-04-29).
//
// Kept separate from vite.config.js so the test runner doesn't share
// the vite dev-server config (which carries the ``proxy`` and
// ``server`` blocks). Both configs use the same react plugin so JSX
// transforms are identical between dev and test.

import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./test/setup.js'],
    include: ['./test/**/*.test.{js,jsx}'],
    // Single-fork pool — the smoke suite is small enough that
    // worker startup dominates total runtime, and the project's
    // tests don't need parallelism.
    pool: 'forks',
    poolOptions: {
      forks: {
        singleFork: true,
      },
    },
    // Defensive cap so a runaway render doesn't tie up CI.
    testTimeout: 10000,
    hookTimeout: 10000,
  },
});
