// Staged-state helpers for IdentityEditFull (D56).
//
// The unified Profile edit form mutates a single `staged` object that
// mirrors the on-disk jumper shape, plus per-row bookkeeping so the
// Phase 5 save orchestrator can compute a diff without a deep-equality
// pass.
//
// Shape:
//
//   staged = {
//     identity: { name: string, exit_weight_lb: string },
//     memberships: [{ ...row, status, original }, ...],
//     cops:        [...],
//     ratings:     [...],
//     tandem_ratings: [...],
//     medicals:    [...],
//   }
//
// Identity values are held as strings because the input element binds
// to text; numeric conversion happens at save time. `exit_weight_updated_at`
// is intentionally not staged — the save orchestrator sets it to today
// on every save (D33 staleness clock).
//
// Per-row `status` is one of:
//
//   'unchanged' — row existed at load and has not been touched. Save
//                 phase emits no call for these.
//   'new'       — added in this edit session. Save phase POSTs.
//   'modified'  — existed at load; some field changed. Save phase PUTs.
//   'deleted'   — existed at load; the user removed it. Save phase DELETEs.
//                 A 'new' row removed before save is spliced out of
//                 the array entirely (never reaches 'deleted').
//
// `original` is the row at load time (or null for `new` rows). Phase 5
// uses it to construct the DELETE/PUT call payloads from authoritative
// data rather than potentially-mutated staged fields.

// Initialize staged state from a loaded jumper record. Every existing
// row starts as `unchanged` with its loaded shape kept as `original`.
export function initStagedJumper(jumper) {
  const tagRow = (row) => ({
    ...row,
    status: 'unchanged',
    original: row,
  });
  return {
    identity: {
      name: jumper.name || '',
      exit_weight_lb: jumper.exit_weight_lb == null
        ? ''
        : String(jumper.exit_weight_lb),
    },
    memberships: (jumper.memberships || []).map(tagRow),
    cops: (jumper.cops || []).map(tagRow),
    ratings: (jumper.ratings || []).map(tagRow),
    tandem_ratings: (jumper.tandem_ratings || []).map(tagRow),
    medicals: (jumper.medicals || []).map(tagRow),
  };
}


// True if anything in `staged` would produce a save call.
//
// Identity dirty: trimmed name differs from original, OR the numeric
// reading of exit_weight differs from original (string comparison
// would mis-flag '200' vs '200.0').
export function computeIsDirty(staged, original) {
  const stagedName = (staged.identity.name || '').trim();
  const origName = (original.name || '').trim();
  if (stagedName !== origName) return true;

  const stagedWeight = parseFloat(staged.identity.exit_weight_lb);
  const origWeight = original.exit_weight_lb;
  // NaN-aware comparison: if either side is NaN and the other isn't,
  // that's dirty. If both are NaN we treat them as equal (empty box
  // matching a never-set field).
  const stagedNaN = Number.isNaN(stagedWeight);
  const origNaN = origWeight == null || Number.isNaN(origWeight);
  if (stagedNaN !== origNaN) return true;
  if (!stagedNaN && stagedWeight !== origWeight) return true;

  for (const coll of COLLECTION_KEYS) {
    if ((staged[coll] || []).some((r) => r.status !== 'unchanged')) {
      return true;
    }
  }
  return false;
}


// Compute the per-collection diff the Phase 5 orchestrator consumes.
// Pure function; returns a structure, does not perform any IO.
//
// Shape:
//
//   {
//     identity: { ...payload }  // null when identity didn't change
//     creates:  { memberships: [body, ...], cops: [...], ... }
//     updates:  { memberships: [{ id, body }, ...], cops: [...], ... }
//     deletes:  { memberships: [id, ...], cops: [...], ... }
//   }
//
// Identity payload mirrors the existing IdentityEdit's PUT body:
// trimmed name (or null) + parsed exit_weight + today's date for
// exit_weight_updated_at. The orchestrator decides whether to PUT
// identity based on `identity != null`.
//
// For collections:
//
//   - `creates` carries POST bodies. The local tmp `id` is stripped
//     because the backend assigns the persisted id and rejecting a
//     client-supplied id is the safer posture. Bookkeeping fields
//     (status, original) are also stripped.
//   - `updates` carries `{ id, body }` pairs. The id is needed for
//     the PUT URL; the body is the same shape POST would have used
//     (no id, no bookkeeping).
//   - `deletes` carries just the row ids — DELETE only needs the id.
export function computeDiff(staged, original) {
  const stagedName = (staged.identity.name || '').trim();
  const origName = (original.name || '').trim();
  const stagedWeight = parseFloat(staged.identity.exit_weight_lb);
  const origWeight = original.exit_weight_lb;
  const stagedNaN = Number.isNaN(stagedWeight);

  const nameChanged = stagedName !== origName;
  const weightChanged = stagedNaN
    ? origWeight != null
    : stagedWeight !== origWeight;

  let identity = null;
  if (nameChanged || weightChanged) {
    const today = new Date().toISOString().slice(0, 10);
    identity = {
      name: stagedName || null,
      // Preserve a parseable number when present; if the user blanked
      // the input we still send what they entered — Phase 5 surfaces
      // any validation error from the backend. computeDiff does not
      // gate on numeric validity.
      exit_weight_lb: stagedNaN ? null : stagedWeight,
      exit_weight_updated_at: today,
    };
  }

  const creates = {};
  const updates = {};
  const deletes = {};

  for (const coll of COLLECTION_KEYS) {
    const rows = staged[coll] || [];
    creates[coll] = rows
      .filter((r) => r.status === 'new')
      .map(toBody);
    updates[coll] = rows
      .filter((r) => r.status === 'modified')
      .map((r) => ({ id: r.id, body: toBody(r) }));
    deletes[coll] = rows
      .filter((r) => r.status === 'deleted')
      .map((r) => r.id);
  }

  return { identity, creates, updates, deletes };
}


// True if no field in the diff would emit any call. Useful for the
// Save button's enabled state — Phase 5 may also call this before
// invoking the orchestrator to no-op cleanly.
export function diffIsEmpty(diff) {
  if (diff.identity != null) return false;
  for (const coll of COLLECTION_KEYS) {
    if (diff.creates[coll].length > 0) return false;
    if (diff.updates[coll].length > 0) return false;
    if (diff.deletes[coll].length > 0) return false;
  }
  return true;
}


// Strip per-row bookkeeping AND the row id. The result is the body
// the backend expects for POST / PUT calls — same shape as the
// existing per-row form components build today (which never include
// id in the payload; id is the URL parameter for PUT, server-assigned
// for POST).
function toBody(row) {
  // eslint-disable-next-line no-unused-vars
  const { id, status, original, ...body } = row;
  return body;
}


// --------------------------------------------------------------------- //
// Mutation helpers (D56 Phase 3b)
// --------------------------------------------------------------------- //
//
// Each helper returns a new staged object (or unchanged staged if the
// operation is a no-op). Callers use them with React's functional
// setState: `setStaged((s) => setRowField(s, 'cops', id, { level: 'a' }))`.
//
// The helpers preserve referential identity for rows they don't touch,
// which lets React's reconciliation skip rerendering inputs the user
// isn't editing — important for input focus stability.

// Apply a field patch to one row in one collection. Flips status from
// 'unchanged' to 'modified'; 'new' / 'modified' / 'deleted' rows keep
// their status (a 'deleted' row that gets patched is a programming
// error in the editor — guard there, not here).
export function setRowField(staged, coll, id, patch) {
  const rows = staged[coll];
  if (!rows) return staged;
  const next = rows.map((r) => {
    if (r.id !== id) return r;
    const updated = { ...r, ...patch };
    if (r.status === 'unchanged') updated.status = 'modified';
    return updated;
  });
  return { ...staged, [coll]: next };
}

// Append a new row to a collection. Caller supplies a `seed` row with
// at least an `id` (a client-side uuid for React keying) and whatever
// initial fields make sense for the row type. Status is stamped to
// 'new'; original is null.
export function addRow(staged, coll, seed) {
  const rows = staged[coll] || [];
  const row = { ...seed, status: 'new', original: null };
  return { ...staged, [coll]: [...rows, row] };
}

// Remove a row. If it was 'new' (never persisted), splice it out
// entirely. Otherwise flip status to 'deleted' so the orchestrator
// emits a DELETE. A second remove on an already-deleted row is a
// no-op.
export function removeRow(staged, coll, id) {
  const rows = staged[coll];
  if (!rows) return staged;
  const target = rows.find((r) => r.id === id);
  if (!target) return staged;
  if (target.status === 'new') {
    return { ...staged, [coll]: rows.filter((r) => r.id !== id) };
  }
  if (target.status === 'deleted') return staged;
  const next = rows.map((r) =>
    r.id === id ? { ...r, status: 'deleted' } : r,
  );
  return { ...staged, [coll]: next };
}


export const COLLECTION_KEYS = [
  'memberships',
  'cops',
  'ratings',
  'tandem_ratings',
  'medicals',
];
