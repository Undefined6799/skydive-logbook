// identityEditOrchestrator — D56 Phase 5.
//
// Pure async function. Given a diff (from computeDiff), a jumperId,
// and an injected API surface, run the per-row write calls in the
// order D56 mandates:
//
//   0. PUT /jumpers/{id}            — identity (if changed)
//   1. DELETE per row, across all five collections
//   2. PUT per row, across all five collections
//   3. POST per row, across all five collections
//
// Rationale for the ordering lives in D56 §"Consequences". In short:
// deletes first free conceptual slots and match user intent regardless
// of what follows; updates run on rows that survived; creates run last
// so a failed add is isolated (no half-referenced new id left behind).
// Identity runs at the very top because an exit-weight change is
// load-bearing for the D45 lineset-wear math the user may glance at
// while the rest of the save sequences.
//
// Within each phase, collections are processed in COLLECTION_KEYS
// order (memberships → cops → ratings → tandem_ratings → medicals);
// rows within a collection are processed in the order the diff hands
// them over. Both orderings are deterministic so partial-save failure
// reporting and retry produce the same result on a given diff +
// failure point.
//
// On first failure, the orchestrator returns immediately with the
// portion of the diff that did not run, packaged into a `remaining`
// slice the caller can re-feed via a retry. Identity is never re-run
// once it succeeded — `remaining.identity` is null in that case.

import { COLLECTION_KEYS } from './identityEditStaged';


// Run the diff. Returns:
//
//   { completed: [op, ...], failed: null, remaining: null }
//     — full success.
//
//   { completed: [op, ...], failed: { phase, coll?, op, id?, error },
//     remaining: <diff slice> }
//     — partial success. The caller can pass `remaining` back as the
//       new `diff` argument on retry, and the orchestrator picks up
//       from the failure point.
//
// `api` shape:
//
//   {
//     updateJumper(jumperId, payload) => Promise,
//     collections: {
//       memberships: { add(jumperId, body), update(jumperId, id, body), delete(jumperId, id) },
//       cops: { ... },
//       ratings: { ... },
//       tandem_ratings: { ... },
//       medicals: { ... },
//     }
//   }
export async function runOrchestrator({ jumperId, diff, api }) {
  const completed = [];

  // Phase 0 — identity PUT.
  if (diff.identity) {
    try {
      await api.updateJumper(jumperId, diff.identity);
      completed.push({ phase: 'identity', op: 'put' });
    } catch (err) {
      return {
        completed,
        failed: { phase: 'identity', op: 'put', error: err },
        // Identity didn't land — retry must include it again. The
        // rest of the diff is also untouched, so pass the original
        // diff through unchanged.
        remaining: diff,
      };
    }
  }

  // Identity is done (or wasn't dirty) — subsequent failure reports
  // a `remaining` whose `identity` is null. We accumulate `remaining`
  // as we go so partial failure produces a slice that is exactly
  // "what hasn't run yet".
  const remaining = {
    identity: null,
    deletes: cloneCollectionMap(diff.deletes),
    updates: cloneCollectionMap(diff.updates),
    creates: cloneCollectionMap(diff.creates),
  };

  // Phase 1 — DELETEs.
  for (const coll of COLLECTION_KEYS) {
    const ids = remaining.deletes[coll];
    while (ids.length > 0) {
      const id = ids[0];
      try {
        await api.collections[coll].delete(jumperId, id);
        completed.push({ phase: 'delete', coll, id });
        ids.shift();
      } catch (err) {
        return {
          completed,
          failed: { phase: 'delete', coll, op: 'delete', id, error: err },
          remaining,
        };
      }
    }
  }

  // Phase 2 — PUTs (collection updates).
  for (const coll of COLLECTION_KEYS) {
    const pairs = remaining.updates[coll];
    while (pairs.length > 0) {
      const { id, body } = pairs[0];
      try {
        await api.collections[coll].update(jumperId, id, body);
        completed.push({ phase: 'update', coll, id });
        pairs.shift();
      } catch (err) {
        return {
          completed,
          failed: { phase: 'update', coll, op: 'update', id, error: err },
          remaining,
        };
      }
    }
  }

  // Phase 3 — POSTs (creates).
  for (const coll of COLLECTION_KEYS) {
    const bodies = remaining.creates[coll];
    while (bodies.length > 0) {
      const body = bodies[0];
      try {
        await api.collections[coll].add(jumperId, body);
        completed.push({ phase: 'create', coll });
        bodies.shift();
      } catch (err) {
        return {
          completed,
          failed: { phase: 'create', coll, op: 'create', error: err },
          remaining,
        };
      }
    }
  }

  return { completed, failed: null, remaining: null };
}


// Shallow-clone the { coll: [...] } shape so the orchestrator can
// shift items off the per-collection arrays without mutating the
// caller's diff. Items inside the arrays are not cloned — they are
// either ids (strings) or { id, body } pairs whose body is treated
// as opaque.
function cloneCollectionMap(map) {
  const out = {};
  for (const coll of COLLECTION_KEYS) {
    out[coll] = [...(map[coll] || [])];
  }
  return out;
}


// True when the orchestrator result represents full success. Useful
// in the calling form's branch logic.
export function isFullSuccess(result) {
  return result.failed === null;
}


// Pretty-print one completed entry. The form's partial-save banner
// uses this to summarise what landed.
export function describeOp(op) {
  if (op.phase === 'identity') return 'Identity (name, exit weight)';
  const collLabels = {
    memberships: 'membership',
    cops: 'CoP',
    ratings: 'rating',
    tandem_ratings: 'tandem rating',
    medicals: 'medical',
  };
  const label = collLabels[op.coll] || op.coll;
  if (op.phase === 'delete') return `Removed ${label}`;
  if (op.phase === 'update') return `Updated ${label}`;
  if (op.phase === 'create') return `Added ${label}`;
  return `${op.phase} ${label}`;
}


// Pretty-print the failure. Surfaces RFC 9457 problem+json detail
// when the error is an ApiError; falls back to the message otherwise.
// The ApiError class lives in api.js but checking the shape (problem
// object with detail/title) is sufficient for display purposes.
export function describeFailure(failure) {
  if (!failure) return '';
  const op = describeOp({ ...failure, phase: failure.phase });
  const err = failure.error;
  const problem = err && err.problem;
  const detail = problem?.detail || err?.message || String(err);
  const title = problem?.title;
  return title ? `${op} — ${title}: ${detail}` : `${op} — ${detail}`;
}


// Adapter that turns the flat per-row functions exported by `../api`
// (addJumperMembership, deleteJumperCop, …) into the collection-keyed
// shape runOrchestrator expects. Centralising the mapping here keeps
// IdentityEditFull free of api-naming knowledge and gives tests a
// single seam to inject a fake.
export function buildOrchestratorApi(api) {
  return {
    updateJumper: api.updateJumper,
    collections: {
      memberships: {
        add: api.addJumperMembership,
        update: api.updateJumperMembership,
        delete: api.deleteJumperMembership,
      },
      cops: {
        add: api.addJumperCop,
        update: api.updateJumperCop,
        delete: api.deleteJumperCop,
      },
      ratings: {
        add: api.addJumperRating,
        update: api.updateJumperRating,
        delete: api.deleteJumperRating,
      },
      tandem_ratings: {
        add: api.addJumperTandemRating,
        update: api.updateJumperTandemRating,
        delete: api.deleteJumperTandemRating,
      },
      medicals: {
        add: api.addJumperMedical,
        update: api.updateJumperMedical,
        delete: api.deleteJumperMedical,
      },
    },
  };
}
