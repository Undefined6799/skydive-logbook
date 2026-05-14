// identityEditOrchestrator — pure-function tests (D56 Phase 5).
//
// These tests pin the call sequencing + partial-failure behaviour
// described in D56. Every test injects a fake `api` so we can both
// observe call order and force specific calls to reject.

import { describe, it, expect, vi } from 'vitest';
import {
  runOrchestrator,
  isFullSuccess,
  describeOp,
  describeFailure,
} from '../src/views/identityEditOrchestrator';


// --------------------------------------------------------------------- //
// Helpers
// --------------------------------------------------------------------- //

// Build a fake api whose every method is a Mock returning a resolved
// promise by default. Tests can then override specific methods with
// .mockRejectedValueOnce or check .mock.calls.
function makeFakeApi() {
  const make = () => ({
    add: vi.fn().mockResolvedValue({}),
    update: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  });
  return {
    updateJumper: vi.fn().mockResolvedValue({}),
    collections: {
      memberships: make(),
      cops: make(),
      ratings: make(),
      tandem_ratings: make(),
      medicals: make(),
    },
  };
}

// Empty per-collection map matching the diff shape.
function emptyMap() {
  return {
    memberships: [],
    cops: [],
    ratings: [],
    tandem_ratings: [],
    medicals: [],
  };
}

// Diff containing at least one op in every phase + collection, useful
// for asserting end-to-end ordering and call counts.
function fullDiff() {
  return {
    identity: { name: 'Alex', exit_weight_lb: 200, exit_weight_updated_at: '2026-05-12' },
    deletes: {
      memberships: ['m-del'],
      cops: ['c-del'],
      ratings: ['r-del'],
      tandem_ratings: ['t-del'],
      medicals: ['med-del'],
    },
    updates: {
      memberships: [{ id: 'm-upd', body: { member_number: 'updated' } }],
      cops: [{ id: 'c-upd', body: { level: 'b' } }],
      ratings: [{ id: 'r-upd', body: { code: 'c1' } }],
      tandem_ratings: [{ id: 't-upd', body: { expiry_date: '2027-01-01' } }],
      medicals: [{ id: 'med-upd', body: { issuing_authority: 'TC' } }],
    },
    creates: {
      memberships: [{ org: 'CSPA', member_number: '999' }],
      cops: [{ org: 'CSPA', level: 'a' }],
      ratings: [{ org: 'CSPA', code: 'c1' }],
      tandem_ratings: [{ system: 'upt_vector' }],
      medicals: [{ kind: 'class_iii', issuing_authority: 'FAA' }],
    },
  };
}


describe('runOrchestrator', () => {
  it('empty diff → no calls, full success', async () => {
    const api = makeFakeApi();
    const result = await runOrchestrator({
      jumperId: 'j1',
      diff: { identity: null, deletes: emptyMap(), updates: emptyMap(), creates: emptyMap() },
      api,
    });
    expect(isFullSuccess(result)).toBe(true);
    expect(result.completed).toEqual([]);
    expect(api.updateJumper).not.toHaveBeenCalled();
  });

  it('full diff calls every phase across every collection in the correct order', async () => {
    const api = makeFakeApi();
    // Track interleaved ordering across all api functions using a
    // single sequence array. Each mock pushes a label on call.
    const order = [];
    api.updateJumper.mockImplementation(async () => { order.push('identity:put'); });
    for (const coll of ['memberships', 'cops', 'ratings', 'tandem_ratings', 'medicals']) {
      api.collections[coll].delete.mockImplementation(async (_, id) => { order.push(`${coll}:delete:${id}`); });
      api.collections[coll].update.mockImplementation(async (_, id) => { order.push(`${coll}:update:${id}`); });
      api.collections[coll].add.mockImplementation(async () => { order.push(`${coll}:create`); });
    }

    const result = await runOrchestrator({ jumperId: 'j1', diff: fullDiff(), api });
    expect(isFullSuccess(result)).toBe(true);
    expect(order).toEqual([
      'identity:put',
      // Deletes — five collections in COLLECTION_KEYS order
      'memberships:delete:m-del',
      'cops:delete:c-del',
      'ratings:delete:r-del',
      'tandem_ratings:delete:t-del',
      'medicals:delete:med-del',
      // Updates — same order
      'memberships:update:m-upd',
      'cops:update:c-upd',
      'ratings:update:r-upd',
      'tandem_ratings:update:t-upd',
      'medicals:update:med-upd',
      // Creates — same order
      'memberships:create',
      'cops:create',
      'ratings:create',
      'tandem_ratings:create',
      'medicals:create',
    ]);
  });

  it('identity PUT failure halts at phase 0; remaining carries the full diff', async () => {
    const api = makeFakeApi();
    api.updateJumper.mockRejectedValueOnce(new Error('boom'));

    const diff = fullDiff();
    const result = await runOrchestrator({ jumperId: 'j1', diff, api });

    expect(isFullSuccess(result)).toBe(false);
    expect(result.completed).toEqual([]);
    expect(result.failed.phase).toBe('identity');
    expect(result.failed.error.message).toBe('boom');
    // Remaining is the original diff verbatim — retry must include
    // identity again.
    expect(result.remaining).toBe(diff);
    // No collection call was attempted.
    for (const coll of ['memberships', 'cops', 'ratings', 'tandem_ratings', 'medicals']) {
      expect(api.collections[coll].delete).not.toHaveBeenCalled();
      expect(api.collections[coll].update).not.toHaveBeenCalled();
      expect(api.collections[coll].add).not.toHaveBeenCalled();
    }
  });

  it('delete-phase failure: identity + prior deletes land; remaining starts at the failed delete', async () => {
    const api = makeFakeApi();
    // Identity OK, memberships delete OK, cops delete FAILS.
    api.collections.cops.delete.mockRejectedValueOnce(new Error('cop-del fail'));

    const diff = fullDiff();
    const result = await runOrchestrator({ jumperId: 'j1', diff, api });

    expect(isFullSuccess(result)).toBe(false);
    expect(result.completed).toEqual([
      { phase: 'identity', op: 'put' },
      { phase: 'delete', coll: 'memberships', id: 'm-del' },
    ]);
    expect(result.failed.phase).toBe('delete');
    expect(result.failed.coll).toBe('cops');
    expect(result.failed.id).toBe('c-del');
    // Remaining identity null (already ran); deletes start at cops
    // (the failed one) and include all later collections; updates +
    // creates untouched.
    expect(result.remaining.identity).toBeNull();
    expect(result.remaining.deletes.memberships).toEqual([]);
    expect(result.remaining.deletes.cops).toEqual(['c-del']);
    expect(result.remaining.deletes.ratings).toEqual(['r-del']);
    expect(result.remaining.updates.memberships).toHaveLength(1);
    expect(result.remaining.creates.memberships).toHaveLength(1);
  });

  it('update-phase failure: deletes all land; remaining starts at the failed update', async () => {
    const api = makeFakeApi();
    api.collections.ratings.update.mockRejectedValueOnce(new Error('rating-upd fail'));

    const diff = fullDiff();
    const result = await runOrchestrator({ jumperId: 'j1', diff, api });

    expect(isFullSuccess(result)).toBe(false);
    expect(result.failed.phase).toBe('update');
    expect(result.failed.coll).toBe('ratings');
    expect(result.failed.id).toBe('r-upd');

    // All deletes landed.
    expect(result.completed.filter((o) => o.phase === 'delete')).toHaveLength(5);
    // Identity landed.
    expect(result.completed[0]).toEqual({ phase: 'identity', op: 'put' });
    // Memberships + CoPs updates landed before ratings failed.
    const updates = result.completed.filter((o) => o.phase === 'update');
    expect(updates).toEqual([
      { phase: 'update', coll: 'memberships', id: 'm-upd' },
      { phase: 'update', coll: 'cops', id: 'c-upd' },
    ]);
    // Remaining: identity null, deletes empty, updates start at
    // ratings, creates untouched.
    expect(result.remaining.identity).toBeNull();
    expect(result.remaining.deletes.memberships).toEqual([]);
    expect(result.remaining.updates.memberships).toEqual([]);
    expect(result.remaining.updates.cops).toEqual([]);
    expect(result.remaining.updates.ratings).toHaveLength(1);
    expect(result.remaining.updates.ratings[0].id).toBe('r-upd');
    expect(result.remaining.creates.memberships).toHaveLength(1);
  });

  it('create-phase failure: everything before landed; remaining is just unrun creates', async () => {
    const api = makeFakeApi();
    api.collections.medicals.add.mockRejectedValueOnce(new Error('med-add fail'));

    const diff = fullDiff();
    const result = await runOrchestrator({ jumperId: 'j1', diff, api });

    expect(result.failed.phase).toBe('create');
    expect(result.failed.coll).toBe('medicals');
    // Five deletes + five updates + four prior creates landed (memberships,
    // cops, ratings, tandem_ratings) before medicals failed; plus
    // identity = 15 ops in completed.
    expect(result.completed).toHaveLength(1 + 5 + 5 + 4);
    expect(result.remaining.identity).toBeNull();
    // Every delete + update slice is empty.
    for (const coll of ['memberships', 'cops', 'ratings', 'tandem_ratings', 'medicals']) {
      expect(result.remaining.deletes[coll]).toEqual([]);
      expect(result.remaining.updates[coll]).toEqual([]);
    }
    // Only medicals creates remain.
    expect(result.remaining.creates.memberships).toEqual([]);
    expect(result.remaining.creates.medicals).toHaveLength(1);
  });

  it('retry on `remaining` after a delete-phase failure picks up cleanly', async () => {
    const api = makeFakeApi();
    api.collections.cops.delete.mockRejectedValueOnce(new Error('first try fails'));

    const diff = fullDiff();
    const first = await runOrchestrator({ jumperId: 'j1', diff, api });
    expect(isFullSuccess(first)).toBe(false);

    // Retry — the mock no longer rejects (only mockRejectedValueOnce).
    const second = await runOrchestrator({
      jumperId: 'j1',
      diff: first.remaining,
      api,
    });
    expect(isFullSuccess(second)).toBe(true);
    // Identity is NOT re-called.
    expect(api.updateJumper).toHaveBeenCalledTimes(1);
    // cops.delete is called once for the retry (the prior failure
    // didn't shift the row off the queue).
    expect(api.collections.cops.delete).toHaveBeenCalledTimes(2);
  });

  it("orchestrator does not mutate the caller's diff object", async () => {
    const api = makeFakeApi();
    api.collections.ratings.update.mockRejectedValueOnce(new Error('fail'));
    const diff = fullDiff();
    const snapshot = JSON.parse(JSON.stringify(diff));
    await runOrchestrator({ jumperId: 'j1', diff, api });
    expect(diff).toEqual(snapshot);
  });

  it('describeOp produces user-readable labels for every phase', () => {
    expect(describeOp({ phase: 'identity', op: 'put' })).toBe('Identity (name, exit weight)');
    expect(describeOp({ phase: 'delete', coll: 'memberships', id: 'm1' })).toBe('Removed membership');
    expect(describeOp({ phase: 'update', coll: 'cops', id: 'c1' })).toBe('Updated CoP');
    expect(describeOp({ phase: 'create', coll: 'ratings' })).toBe('Added rating');
    expect(describeOp({ phase: 'create', coll: 'tandem_ratings' })).toBe('Added tandem rating');
    expect(describeOp({ phase: 'create', coll: 'medicals' })).toBe('Added medical');
  });

  it('describeFailure includes the problem+json title/detail when present', () => {
    const err = Object.assign(new Error('http 422'), {
      problem: { title: 'Validation failed', detail: 'expiry_date is required' },
    });
    const out = describeFailure({ phase: 'update', coll: 'memberships', id: 'm1', error: err });
    expect(out).toContain('Updated membership');
    expect(out).toContain('Validation failed');
    expect(out).toContain('expiry_date is required');
  });

  it('describeFailure falls back to error.message when no problem present', () => {
    const out = describeFailure({
      phase: 'delete',
      coll: 'cops',
      id: 'c1',
      error: new Error('network unreachable'),
    });
    expect(out).toContain('Removed CoP');
    expect(out).toContain('network unreachable');
  });
});
