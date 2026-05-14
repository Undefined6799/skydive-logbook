// Per-view import + render smoke tests — TEST-8 (audit 2026-04-29).
//
// One assertion per view:
//   1. The default export imports without throwing.
//   2. ``render(<View />)`` returns without throwing on the first
//      render pass — i.e. the component's hooks fire, its children
//      mount, and no immediate prop / hook / module-resolution
//      bug surfaces.
//
// We DO NOT query for any specific text / element. Views render
// against mocked api modules in a "loading" state — the actual
// rendered output depends on which mocks return what, and pinning
// that here would push this beyond the audit's scope ("catch import
// drift", not "verify behaviour").
//
// Rationale: the static-typing safety net stops at the JS file
// boundary. JS's late binding lets a renamed default export, a
// dropped Lucide icon, or an undefined hook ride into production
// without a single warning until the user hits the affected view.
// One render pass per view catches that class of bug at CI time.

import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import React from 'react';

// Stub the api module — every view top-level imports from it. The
// stubbed functions return promises that never resolve, so the
// view stays in its loading branch and we don't have to model
// every possible API response shape.
// We don't ``vi.mock('../src/api', ...)`` — vitest's module mocker
// requires every named export be enumerable, and ``api.js`` exports
// 50+ functions. Re-listing them in the mock would invert the
// "catches drift" goal: every new endpoint would need a mock entry
// or the smoke test breaks for the wrong reason.
//
// Instead we let the real api module load (no IO at module init —
// it's all top-level ``export async function`` declarations) and
// stub the global ``fetch`` (already done in test/setup.js). The
// view's ``useEffect`` calls hit the never-resolving fetch and
// stay in their loading branch — exactly the smoke we want.

// Stub modal subtrees — they import the same api module already
// stubbed above, but they also typically pull in date-pickers / form
// libraries that are unrelated to the smoke surface.
vi.mock('../src/modals/LogJumpModal', () => ({
  default: () => null,
}));
vi.mock('../src/modals/JumpDetailModal', () => ({
  default: () => null,
}));
vi.mock('../src/modals/ComponentDetailModal', () => ({
  default: () => null,
}));
vi.mock('../src/modals/AddRigModal', () => ({
  default: () => null,
}));
vi.mock('../src/modals/AddComponentModal', () => ({
  default: () => null,
}));
vi.mock('../src/modals/DropzoneModal', () => ({
  default: () => null,
}));

// Import the views AFTER vi.mock so the mocks are in place when
// the view modules' top-level code runs.
import Profile from '../src/views/Profile';
import Jumps from '../src/views/Jumps';
import MyRig from '../src/views/MyRig';
import Inventory from '../src/views/Inventory';
import Dropzones from '../src/views/Dropzones';
import Settings from '../src/views/Settings';
import CareerStats from '../src/views/CareerStats';

const VIEWS = [
  ['Profile', Profile],
  ['Jumps', Jumps],
  ['MyRig', MyRig],
  ['Inventory', Inventory],
  ['Dropzones', Dropzones],
  ['Settings', Settings],
  ['CareerStats', CareerStats],
];

describe('view import + render smoke', () => {
  for (const [name, Component] of VIEWS) {
    it(`${name} renders without throwing`, () => {
      // Pin: the default export is a function. A regression that
      // changes it to an object / class / undefined would surface
      // here before render even fires.
      expect(typeof Component).toBe('function');
      // Pin: first render pass completes. The exact rendered
      // markup is intentionally unasserted.
      const { container } = render(<Component />);
      expect(container).toBeTruthy();
    });
  }
});
