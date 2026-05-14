// D60 starred-dropzone — frontend wiring tests.
//
// Covers three slices of the D60 frontend rollout:
//
//   1. ``starDropzone(id)`` API helper in src/api.js — URL +
//      method correctness; idempotent invocation.
//   2. ``LogJumpModal`` preselect — when the loaded DZ list has a
//      starred entry and this is a brand-new jump (!isEdit), the
//      form's ``dropzone_id`` auto-fills to the starred DZ's id.
//      Mirrors the D58 rig preselect already in place.
//   3. ``Dropzones`` view — clicking the star button on a
//      non-starred DZ calls ``starDropzone`` and re-renders so
//      exactly one DZ shows the starred state.
//
// The tests stub ``globalThis.fetch`` per setup.js's pattern.
// We use a tiny per-test fetch router that matches on method+URL
// rather than vi.mock('../src/api', ...) because the api module
// exports 50+ functions and re-listing them inverts the smoke
// test's "catches drift" goal.

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { starDropzone } from '../src/api';
import Dropzones from '../src/views/Dropzones';


// Helper: install a fetch stub that routes a small table of
// (method, URL-suffix) tuples to JSON responses or errors. Anything
// unmatched returns a never-resolving promise so the loading branch
// stays mounted (matches setup.js's default posture).
function installFetchRouter(routes) {
  globalThis.fetch = vi.fn((url, init = {}) => {
    const method = (init.method || 'GET').toUpperCase();
    for (const route of routes) {
      if (route.method === method && url.includes(route.pathSuffix)) {
        const body = typeof route.body === 'function' ? route.body() : route.body;
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: route.status || 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        );
      }
    }
    return new Promise(() => {});
  });
}


// Restore the default never-resolving fetch from setup.js so other
// tests in the suite aren't affected by a leaked router.
afterEach(() => {
  globalThis.fetch = vi.fn(() => new Promise(() => {}));
});


// --------------------------------------------------------------------- //
// 1. starDropzone API helper
// --------------------------------------------------------------------- //

describe('starDropzone()', () => {
  it('issues PUT to /api/v1/dropzones/{id}/star', async () => {
    const id = '11111111-1111-1111-1111-111111111111';
    installFetchRouter([
      {
        method: 'PUT',
        pathSuffix: `/api/v1/dropzones/${id}/star`,
        body: { id, name: 'X', city: 'Y', country: 'CA', environment: 'clean_grass', starred: true },
      },
    ]);
    const result = await starDropzone(id);
    expect(result.starred).toBe(true);
    expect(result.id).toBe(id);
    // fetch was called with the right URL + method.
    const [calledUrl, calledInit] = globalThis.fetch.mock.calls[0];
    expect(calledUrl).toMatch(new RegExp(`/api/v1/dropzones/${id}/star$`));
    expect(calledInit.method).toBe('PUT');
  });

  it('idempotent — same call shape on the second invocation', async () => {
    const id = '22222222-2222-2222-2222-222222222222';
    installFetchRouter([
      {
        method: 'PUT',
        pathSuffix: `/api/v1/dropzones/${id}/star`,
        body: { id, name: 'X', city: 'Y', country: 'CA', environment: 'clean_grass', starred: true },
      },
    ]);
    await starDropzone(id);
    await starDropzone(id);
    expect(globalThis.fetch).toHaveBeenCalledTimes(2);
    // Both calls share the same URL + method.
    for (const [url, init] of globalThis.fetch.mock.calls) {
      expect(url).toMatch(new RegExp(`/api/v1/dropzones/${id}/star$`));
      expect(init.method).toBe('PUT');
    }
  });
});


// --------------------------------------------------------------------- //
// 2. Dropzones view — star button click triggers PUT and re-renders
// --------------------------------------------------------------------- //

describe('Dropzones view — star toggle', () => {
  // Two DZs: first is starred, second is not. Clicking the second's
  // star button should call PUT /dropzones/{second.id}/star and
  // result in exactly one starred DZ in the rendered list.
  const FIRST = {
    id: '11111111-1111-1111-1111-111111111111',
    name: 'Alpha DZ',
    city: 'Lake A',
    country: 'CA',
    environment: 'clean_grass',
    starred: true,
    aircraft: [],
  };
  const SECOND = {
    id: '22222222-2222-2222-2222-222222222222',
    name: 'Bravo DZ',
    city: 'Lake B',
    country: 'CA',
    environment: 'clean_grass',
    starred: false,
    aircraft: [],
  };

  beforeEach(() => {
    // Routes: list returns both summaries; each get returns the full
    // record (page hydrates summaries → fulls per R.D.6); star
    // returns the updated DZ. The list URL ends in ``/dropzones``;
    // each get/star request matches a unique suffix.
    installFetchRouter([
      // listDropzones — query string varies; pathSuffix matches
      // before the ``?``.
      {
        method: 'GET',
        pathSuffix: '/api/v1/dropzones?',
        body: [
          { id: FIRST.id, name: FIRST.name, city: FIRST.city, country: FIRST.country, environment: FIRST.environment, starred: true },
          { id: SECOND.id, name: SECOND.name, city: SECOND.city, country: SECOND.country, environment: SECOND.environment, starred: false },
        ],
      },
      { method: 'GET', pathSuffix: `/api/v1/dropzones/${FIRST.id}`, body: FIRST },
      { method: 'GET', pathSuffix: `/api/v1/dropzones/${SECOND.id}`, body: SECOND },
      {
        method: 'PUT',
        pathSuffix: `/api/v1/dropzones/${SECOND.id}/star`,
        body: { ...SECOND, starred: true },
      },
    ]);
  });

  // Per the 2026-05 design-system rollout the per-card action row
  // collapsed into a single ⋯ overflow menu; the star / edit / delete
  // entries live inside the popover that opens on click. The tests
  // here drive that flow: find the menu trigger by its aria-label,
  // click it, then assert on the menu items.
  function openMenu(dzName) {
    const trigger = screen.getByLabelText(new RegExp(`Actions for ${dzName}`, 'i'));
    fireEvent.click(trigger);
    return trigger;
  }

  it('renders both DZs and surfaces the starred state', async () => {
    render(<Dropzones />);
    await waitFor(() => screen.getByText('Alpha DZ'));
    await waitFor(() => screen.getByText('Bravo DZ'));
    // Open Alpha's menu — starred DZ shows "Default" entry.
    openMenu('Alpha DZ');
    expect(screen.getByText('Default')).toBeTruthy();
    // Re-render Bravo's menu — non-starred DZ shows "Set as default".
    openMenu('Bravo DZ');
    expect(screen.getByText('Set as default')).toBeTruthy();
  });

  it('clicking a non-starred DZ\'s "Set as default" calls PUT and moves the star', async () => {
    render(<Dropzones />);
    await waitFor(() => screen.getByText('Bravo DZ'));
    openMenu('Bravo DZ');
    const setDefault = screen.getByText('Set as default');
    fireEvent.click(setDefault);
    await waitFor(() => {
      const putCalls = globalThis.fetch.mock.calls.filter(
        ([url, init]) =>
          init?.method === 'PUT' && url.endsWith(`/api/v1/dropzones/${SECOND.id}/star`),
      );
      expect(putCalls.length).toBeGreaterThan(0);
    });
    // After the API resolves, opening Bravo's menu shows "Default".
    await waitFor(() => {
      openMenu('Bravo DZ');
      expect(screen.getByText('Default')).toBeTruthy();
    });
  });

  it('clicking the already-starred DZ\'s "Default" entry is a no-op (no PUT)', async () => {
    render(<Dropzones />);
    await waitFor(() => screen.getByText('Alpha DZ'));
    openMenu('Alpha DZ');
    const defaultEntry = screen.getByText('Default');
    fireEvent.click(defaultEntry);
    await new Promise((r) => setTimeout(r, 30));
    const putCalls = globalThis.fetch.mock.calls.filter(
      ([, init]) => init?.method === 'PUT',
    );
    expect(putCalls).toHaveLength(0);
  });
});
