// Vitest setup — TEST-8 (audit 2026-04-29).
//
// Runs before every test file. Adds jest-dom's matchers
// (toBeInTheDocument, toBeVisible, etc.) and stubs the browser APIs
// jsdom doesn't ship.

import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

// Each test gets a fresh DOM — without this, render() leaks elements
// into subsequent tests' queries. The render-and-import smoke checks
// don't query for elements but the cleanup is cheap insurance against
// future test additions.
afterEach(() => {
  cleanup();
});

// jsdom's Window.localStorage is sometimes a plain object instead of
// the Storage prototype, which makes ``localStorage.getItem`` undefined
// inside hooks like ``useAltitudeUnit``. Provide a Map-backed shim with
// the full Storage surface so hooks that read/write preferences mount
// cleanly under tests.
if (typeof window !== 'undefined' && (
  !window.localStorage
  || typeof window.localStorage.getItem !== 'function'
)) {
  const store = new Map();
  const shim = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => { store.set(k, String(v)); },
    removeItem: (k) => { store.delete(k); },
    clear: () => { store.clear(); },
    key: (i) => Array.from(store.keys())[i] ?? null,
    get length() { return store.size; },
  };
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: shim,
  });
}

// Some views call window.matchMedia (e.g. for prefers-color-scheme).
// jsdom doesn't ship it; provide a no-op matchMedia so component
// mount doesn't blow up.
if (!window.matchMedia) {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  });
}

// Views start a fetch on mount. The test goal is "the import + first
// render survives," not "the API behaves." Stub the global fetch with
// a never-resolving promise so the loading-state branch renders and
// the test finishes immediately. (A resolved-empty stub also works
// but a never-resolve avoids inadvertently entering downstream
// branches that may have their own brittle expectations.)
if (!globalThis.fetch || vi.isMockFunction(globalThis.fetch) === false) {
  globalThis.fetch = vi.fn(() => new Promise(() => {}));
}
