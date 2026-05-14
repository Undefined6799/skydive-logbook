// IdentityEditFull — Phase 3a scaffold test (D56).
//
// What this asserts:
//   1. The staged-state helpers behave correctly on a representative
//      jumper fixture (pure-function tests; no React).
//   2. The component mounts with the loaded jumper's identity values.
//   3. Typing into the name input dirties the form.
//   4. Cancel without edits skips the confirm() prompt.
//   5. Cancel after edits triggers the confirm() prompt; choosing
//      "no" keeps the form open (onCancel not invoked).
//   6. Save with no edits is a no-op (onSaved not invoked).
//   7. Save after edits invokes onSaved with a diff carrying the
//      identity changes.
//
// Phase 3b will extend this file with assertions on the associations
// editor. Phase 5 will add tests for the save orchestrator at a
// different boundary.

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

import IdentityEditFull from '../src/views/IdentityEditFull';
import {
  initStagedJumper,
  computeIsDirty,
  computeDiff,
  diffIsEmpty,
  setRowField,
  addRow,
  removeRow,
  COLLECTION_KEYS,
} from '../src/views/identityEditStaged';


// Build a flat fake api object matching the shape of `../src/api`.
// Every method is a vi.fn returning a resolved empty object by
// default. Tests grab a reference and inject it into IdentityEditFull
// via the `api` prop so they can assert against per-row calls or
// force specific rejections.
function makeFakeApi() {
  const ok = () => vi.fn().mockResolvedValue({});
  return {
    updateJumper: ok(),
    addJumperMembership: ok(),
    updateJumperMembership: ok(),
    deleteJumperMembership: ok(),
    addJumperCop: ok(),
    updateJumperCop: ok(),
    deleteJumperCop: ok(),
    addJumperRating: ok(),
    updateJumperRating: ok(),
    deleteJumperRating: ok(),
    addJumperTandemRating: ok(),
    updateJumperTandemRating: ok(),
    deleteJumperTandemRating: ok(),
    addJumperMedical: ok(),
    updateJumperMedical: ok(),
    deleteJumperMedical: ok(),
  };
}


// --------------------------------------------------------------------- //
// Fixture
// --------------------------------------------------------------------- //

function makeJumper(overrides = {}) {
  return {
    id: 'jumper-1',
    name: 'Alex Pilot',
    exit_weight_lb: 200,
    exit_weight_updated_at: '2025-09-01',
    memberships: [
      {
        id: 'm1',
        org: 'CSPA',
        member_number: '23572',
        expiry_date: '2026-07-11',
      },
    ],
    cops: [
      { id: 'c1', org: 'CSPA', level: 'solo', issued_date: '2022-10-05' },
    ],
    ratings: [
      { id: 'r1', org: 'CSPA', code: 'c2', expiry_date: '2026-04-24' },
    ],
    tandem_ratings: [
      { id: 't1', system: 'upt_sigma', expiry_date: '2026-04-30' },
    ],
    medicals: [],
    attachments: [],
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-09-01T00:00:00Z',
    ...overrides,
  };
}


// --------------------------------------------------------------------- //
// Pure helper tests — initStagedJumper / computeIsDirty / computeDiff
// --------------------------------------------------------------------- //

describe('identityEditStaged helpers', () => {
  it('initStagedJumper tags every row unchanged with original snapshot', () => {
    const j = makeJumper();
    const s = initStagedJumper(j);

    expect(s.identity).toEqual({ name: 'Alex Pilot', exit_weight_lb: '200' });
    for (const k of COLLECTION_KEYS) {
      for (const row of s[k]) {
        expect(row.status).toBe('unchanged');
        expect(row.original).toBeDefined();
        expect(row.original.id).toBe(row.id);
      }
    }
  });

  it('initStagedJumper handles missing collections + null exit weight', () => {
    const j = makeJumper({
      memberships: undefined,
      cops: undefined,
      ratings: undefined,
      tandem_ratings: undefined,
      medicals: undefined,
      exit_weight_lb: null,
    });
    const s = initStagedJumper(j);
    expect(s.memberships).toEqual([]);
    expect(s.cops).toEqual([]);
    expect(s.identity.exit_weight_lb).toBe('');
  });

  it('computeIsDirty: false on freshly initialized staged', () => {
    const j = makeJumper();
    expect(computeIsDirty(initStagedJumper(j), j)).toBe(false);
  });

  it('computeIsDirty: true when name changed', () => {
    const j = makeJumper();
    const s = initStagedJumper(j);
    s.identity.name = 'Alex Renamed';
    expect(computeIsDirty(s, j)).toBe(true);
  });

  it('computeIsDirty: false when name differs only by surrounding whitespace', () => {
    // The user re-types the same name with stray spaces — trim should
    // not flag that as dirty.
    const j = makeJumper();
    const s = initStagedJumper(j);
    s.identity.name = '  Alex Pilot  ';
    expect(computeIsDirty(s, j)).toBe(false);
  });

  it('computeIsDirty: true when exit weight changed numerically', () => {
    const j = makeJumper();
    const s = initStagedJumper(j);
    s.identity.exit_weight_lb = '205';
    expect(computeIsDirty(s, j)).toBe(true);
  });

  it("computeIsDirty: false when exit weight string parses to the same number ('200' vs '200.0')", () => {
    const j = makeJumper();
    const s = initStagedJumper(j);
    s.identity.exit_weight_lb = '200.0';
    expect(computeIsDirty(s, j)).toBe(false);
  });

  it('computeIsDirty: true when a row is marked new / modified / deleted', () => {
    const j = makeJumper();
    let s = initStagedJumper(j);
    s = { ...s, memberships: [...s.memberships, { id: 'm-new', status: 'new', original: null }] };
    expect(computeIsDirty(s, j)).toBe(true);

    s = initStagedJumper(j);
    s.cops[0].status = 'modified';
    expect(computeIsDirty(s, j)).toBe(true);

    s = initStagedJumper(j);
    s.ratings[0].status = 'deleted';
    expect(computeIsDirty(s, j)).toBe(true);
  });

  it('computeDiff: empty diff when nothing changed', () => {
    const j = makeJumper();
    const diff = computeDiff(initStagedJumper(j), j);
    expect(diff.identity).toBeNull();
    expect(diffIsEmpty(diff)).toBe(true);
  });

  it('computeDiff: identity payload mirrors legacy IdentityEdit shape', () => {
    const j = makeJumper();
    const s = initStagedJumper(j);
    s.identity.name = 'Alex Renamed';
    s.identity.exit_weight_lb = '205';
    const diff = computeDiff(s, j);

    expect(diff.identity).not.toBeNull();
    expect(diff.identity.name).toBe('Alex Renamed');
    expect(diff.identity.exit_weight_lb).toBe(205);
    // exit_weight_updated_at is today's date in YYYY-MM-DD form.
    expect(diff.identity.exit_weight_updated_at).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it('computeDiff: name trimmed; empty name maps to null', () => {
    const j = makeJumper();
    const s = initStagedJumper(j);
    s.identity.name = '   ';
    const diff = computeDiff(s, j);
    expect(diff.identity).not.toBeNull();
    expect(diff.identity.name).toBeNull();
  });

  it('computeDiff: groups rows into creates / updates / deletes per collection', () => {
    const j = makeJumper();
    const s = initStagedJumper(j);
    // mutate the cop: modified
    s.cops[0].status = 'modified';
    s.cops[0].level = 'a';
    // delete the rating
    s.ratings[0].status = 'deleted';
    // add a new medical
    s.medicals.push({
      id: 'tmp-new',
      kind: 'class_iii',
      issuing_authority: 'TC',
      expiry_date: '2027-05-01',
      status: 'new',
      original: null,
    });
    const diff = computeDiff(s, j);
    expect(diff.creates.medicals).toHaveLength(1);
    expect(diff.creates.medicals[0]).not.toHaveProperty('status');
    expect(diff.creates.medicals[0]).not.toHaveProperty('original');
    expect(diff.creates.medicals[0]).not.toHaveProperty('id');
    expect(diff.creates.medicals[0].issuing_authority).toBe('TC');
    expect(diff.updates.cops).toHaveLength(1);
    expect(diff.updates.cops[0].id).toBe('c1');
    expect(diff.updates.cops[0].body.level).toBe('a');
    expect(diff.updates.cops[0].body).not.toHaveProperty('id');
    expect(diff.updates.cops[0].body).not.toHaveProperty('status');
    expect(diff.deletes.ratings).toEqual(['r1']);
  });
});


// --------------------------------------------------------------------- //
// Mutation helper tests (D56 Phase 3b)
// --------------------------------------------------------------------- //

describe('identityEditStaged mutations', () => {
  it("setRowField flips 'unchanged' → 'modified' and applies the patch", () => {
    const j = makeJumper();
    let s = initStagedJumper(j);
    s = setRowField(s, 'cops', 'c1', { level: 'a' });
    const row = s.cops.find((r) => r.id === 'c1');
    expect(row.status).toBe('modified');
    expect(row.level).toBe('a');
    expect(row.original.level).toBe('solo');  // original preserved
  });

  it("setRowField keeps 'new' status when patching a new row", () => {
    const j = makeJumper();
    let s = initStagedJumper(j);
    s = addRow(s, 'ratings', { id: 'tmp-1', org: 'CSPA', code: 'c1', expiry_date: '' });
    s = setRowField(s, 'ratings', 'tmp-1', { expiry_date: '2027-01-01' });
    const row = s.ratings.find((r) => r.id === 'tmp-1');
    expect(row.status).toBe('new');
    expect(row.expiry_date).toBe('2027-01-01');
  });

  it('setRowField on an unknown row id returns staged unchanged', () => {
    const j = makeJumper();
    const s = initStagedJumper(j);
    const next = setRowField(s, 'cops', 'no-such-id', { level: 'b' });
    expect(next).toEqual(s);
  });

  it("addRow appends a new row with status='new' and original=null", () => {
    const j = makeJumper();
    let s = initStagedJumper(j);
    s = addRow(s, 'ratings', { id: 'tmp-new', org: 'CSPA', code: 'c2', expiry_date: '' });
    const row = s.ratings.find((r) => r.id === 'tmp-new');
    expect(row.status).toBe('new');
    expect(row.original).toBeNull();
    expect(row.code).toBe('c2');
  });

  it("removeRow splices 'new' rows out entirely", () => {
    const j = makeJumper();
    let s = initStagedJumper(j);
    s = addRow(s, 'cops', { id: 'tmp-x', org: 'CSPA', level: 'a', issued_date: '' });
    expect(s.cops.find((r) => r.id === 'tmp-x')).toBeDefined();
    s = removeRow(s, 'cops', 'tmp-x');
    expect(s.cops.find((r) => r.id === 'tmp-x')).toBeUndefined();
  });

  it("removeRow flips 'unchanged' to 'deleted' (keeps the row in the array)", () => {
    const j = makeJumper();
    const s = removeRow(initStagedJumper(j), 'cops', 'c1');
    const row = s.cops.find((r) => r.id === 'c1');
    expect(row).toBeDefined();
    expect(row.status).toBe('deleted');
  });

  it('removeRow on an already-deleted row is a no-op', () => {
    const j = makeJumper();
    let s = removeRow(initStagedJumper(j), 'cops', 'c1');
    const before = s;
    s = removeRow(s, 'cops', 'c1');
    expect(s).toBe(before);
  });

  it('mutation helpers preserve referential identity for untouched rows', () => {
    // React relies on === to skip rerenders. Editing one row should
    // not invalidate sibling rows' identities.
    const j = makeJumper({
      cops: [
        { id: 'c1', org: 'CSPA', level: 'solo', issued_date: '2022-10-05' },
        { id: 'c2', org: 'CSPA', level: 'a', issued_date: '2023-04-01' },
      ],
    });
    const s = initStagedJumper(j);
    const c2Before = s.cops.find((r) => r.id === 'c2');
    const next = setRowField(s, 'cops', 'c1', { level: 'b' });
    const c2After = next.cops.find((r) => r.id === 'c2');
    expect(c2After).toBe(c2Before);
  });
});


// --------------------------------------------------------------------- //
// Component behaviour
// --------------------------------------------------------------------- //

describe('IdentityEditFull component', () => {
  let confirmSpy;

  beforeEach(() => {
    confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
  });
  afterEach(() => {
    confirmSpy.mockRestore();
  });

  it('mounts with the loaded jumper identity values', () => {
    const jumper = makeJumper();
    render(<IdentityEditFull jumper={jumper} onCancel={() => {}} onSaved={() => {}} />);

    const nameInput = screen.getByLabelText('Name');
    const weightInput = screen.getByLabelText('Exit weight');
    expect(nameInput.value).toBe('Alex Pilot');
    expect(weightInput.value).toBe('200');
  });

  it('Cancel without edits skips the confirm prompt and invokes onCancel', () => {
    const onCancel = vi.fn();
    render(<IdentityEditFull jumper={makeJumper()} onCancel={onCancel} onSaved={() => {}} />);

    fireEvent.click(screen.getByText('Cancel'));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('Cancel after edits triggers confirm; rejecting keeps the form open', () => {
    const onCancel = vi.fn();
    confirmSpy.mockReturnValue(false);  // user picks "no, keep editing"

    render(<IdentityEditFull jumper={makeJumper()} onCancel={onCancel} onSaved={() => {}} />);
    const nameInput = screen.getByLabelText('Name');
    fireEvent.change(nameInput, { target: { value: 'Alex Renamed' } });

    fireEvent.click(screen.getByText('Cancel'));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('Cancel after edits triggers confirm; accepting invokes onCancel', () => {
    const onCancel = vi.fn();
    confirmSpy.mockReturnValue(true);

    render(<IdentityEditFull jumper={makeJumper()} onCancel={onCancel} onSaved={() => {}} />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Alex Renamed' } });
    fireEvent.click(screen.getByText('Cancel'));

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('Save with no edits does not call onSaved (and onCancel handles the empty close)', async () => {
    const onSaved = vi.fn();
    const onCancel = vi.fn();
    render(<IdentityEditFull jumper={makeJumper()} onCancel={onCancel} onSaved={onSaved} />);

    // Save button is disabled when nothing is dirty — clicking it is
    // a no-op via the disabled prop. Submit via the form element
    // anyway to exercise the handleSave guard.
    const form = screen.getByText('EDIT IDENTITY').closest('form');
    fireEvent.submit(form);

    expect(onSaved).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('Save after edits invokes updateJumper with the identity payload, then onSaved', async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined);
    const api = makeFakeApi();
    render(<IdentityEditFull jumper={makeJumper()} onCancel={() => {}} onSaved={onSaved} api={api} />);

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Alex Renamed' } });
    fireEvent.change(screen.getByLabelText('Exit weight'), { target: { value: '210' } });

    // The Save handler is async (orchestrator runs inside). Wrap the
    // click in act() so React flushes the partial/finished state.
    await act(async () => {
      fireEvent.click(screen.getByText('Save'));
    });

    expect(api.updateJumper).toHaveBeenCalledTimes(1);
    const [calledJumperId, payload] = api.updateJumper.mock.calls[0];
    expect(calledJumperId).toBe('jumper-1');
    expect(payload.name).toBe('Alex Renamed');
    expect(payload.exit_weight_lb).toBe(210);
    expect(payload.exit_weight_updated_at).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(onSaved).toHaveBeenCalledTimes(1);
  });
});


// --------------------------------------------------------------------- //
// AssociationsEditor behaviour (D56 Phase 3b)
// --------------------------------------------------------------------- //

describe('IdentityEditFull associations editor', () => {
  let confirmSpy;
  beforeEach(() => {
    confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
  });
  afterEach(() => {
    confirmSpy.mockRestore();
  });

  it('editing a membership member_number issues a PUT membership call', async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined);
    const api = makeFakeApi();
    render(<IdentityEditFull jumper={makeJumper()} onCancel={() => {}} onSaved={onSaved} api={api} />);

    const memberInput = screen.getByLabelText('Member number');
    fireEvent.change(memberInput, { target: { value: '99999' } });
    expect(screen.getByText('EDITED')).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByText('Save'));
    });

    expect(api.updateJumperMembership).toHaveBeenCalledTimes(1);
    const [j, mId, body] = api.updateJumperMembership.mock.calls[0];
    expect(j).toBe('jumper-1');
    expect(mId).toBe('m1');
    expect(body.member_number).toBe('99999');
    expect(body).not.toHaveProperty('id');
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it('clicking Delete on a CoP row issues a DELETE cop call', async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined);
    const api = makeFakeApi();
    render(<IdentityEditFull jumper={makeJumper()} onCancel={() => {}} onSaved={onSaved} api={api} />);

    fireEvent.click(screen.getByLabelText('Delete CoP'));

    await act(async () => {
      fireEvent.click(screen.getByText('Save'));
    });
    expect(api.deleteJumperCop).toHaveBeenCalledTimes(1);
    expect(api.deleteJumperCop.mock.calls[0]).toEqual(['jumper-1', 'c1']);
    expect(api.updateJumperCop).not.toHaveBeenCalled();
    expect(api.addJumperCop).not.toHaveBeenCalled();
  });

  it('Add Rating issues a POST rating call with the user-entered fields', async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined);
    const api = makeFakeApi();
    render(<IdentityEditFull jumper={makeJumper()} onCancel={() => {}} onSaved={onSaved} api={api} />);

    fireEvent.click(screen.getByText('Add Rating'));
    const codeSelects = screen.getAllByLabelText('Rating code');
    const newSelect = codeSelects.find((el) => el.value === '');
    fireEvent.change(newSelect, { target: { value: 'c1' } });
    const dateInputs = screen.getAllByLabelText('Expiry date');
    const newDate = dateInputs.find((el) => el.value === '');
    fireEvent.change(newDate, { target: { value: '2027-06-01' } });

    await act(async () => {
      fireEvent.click(screen.getByText('Save'));
    });
    expect(api.addJumperRating).toHaveBeenCalledTimes(1);
    const [j, body] = api.addJumperRating.mock.calls[0];
    expect(j).toBe('jumper-1');
    expect(body.code).toBe('c1');
    expect(body.expiry_date).toBe('2027-06-01');
    expect(body.org).toBe('CSPA');
    expect(body).not.toHaveProperty('id');
  });

  it('Add Association issues a POST membership under the picked org', async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined);
    const api = makeFakeApi();
    render(
      <IdentityEditFull
        jumper={makeJumper({ memberships: [], cops: [], ratings: [] })}
        onCancel={() => {}}
        onSaved={onSaved}
        api={api}
      />,
    );

    fireEvent.click(screen.getByLabelText('Add Association'));
    fireEvent.change(screen.getByLabelText('Association org'), { target: { value: 'USPA' } });
    fireEvent.click(screen.getByLabelText('Confirm add association'));

    fireEvent.change(screen.getByLabelText('Member number'), { target: { value: '7777' } });
    fireEvent.change(screen.getByLabelText('Expiry date'), { target: { value: '2027-12-31' } });

    await act(async () => {
      fireEvent.click(screen.getByText('Save'));
    });
    expect(api.addJumperMembership).toHaveBeenCalledTimes(1);
    const [, body] = api.addJumperMembership.mock.calls[0];
    expect(body.org).toBe('USPA');
    expect(body.member_number).toBe('7777');
    expect(body.expiry_date).toBe('2027-12-31');
  });

  it('adding then removing a new row in the same session emits no call for it', async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined);
    const api = makeFakeApi();
    render(<IdentityEditFull jumper={makeJumper()} onCancel={() => {}} onSaved={onSaved} api={api} />);

    fireEvent.click(screen.getByText('Add Rating'));
    const trashes = screen.getAllByLabelText('Delete rating');
    expect(trashes).toHaveLength(2);
    fireEvent.click(trashes[1]);

    // Dirty the form via name so Save isn't a no-op.
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Alex Renamed' } });

    await act(async () => {
      fireEvent.click(screen.getByText('Save'));
    });
    expect(api.addJumperRating).not.toHaveBeenCalled();
    expect(api.updateJumper).toHaveBeenCalledTimes(1);
  });
});


// --------------------------------------------------------------------- //
// TandemRatingsEditor + MedicalsEditor behaviour (D56 Phase 4)
// --------------------------------------------------------------------- //

describe('IdentityEditFull tandem ratings + medicals editors', () => {
  let confirmSpy;
  beforeEach(() => {
    confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
  });
  afterEach(() => {
    confirmSpy.mockRestore();
  });

  it("editing a tandem rating's expiry issues a PUT tandem-rating call", async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined);
    const api = makeFakeApi();
    render(<IdentityEditFull jumper={makeJumper()} onCancel={() => {}} onSaved={onSaved} api={api} />);

    fireEvent.change(screen.getByLabelText('Tandem expiry date'), {
      target: { value: '2027-04-30' },
    });
    await act(async () => {
      fireEvent.click(screen.getByText('Save'));
    });
    expect(api.updateJumperTandemRating).toHaveBeenCalledTimes(1);
    const [, tId, body] = api.updateJumperTandemRating.mock.calls[0];
    expect(tId).toBe('t1');
    expect(body.expiry_date).toBe('2027-04-30');
    expect(body.system).toBe('upt_sigma');
  });

  it('deleting an existing tandem rating issues a DELETE call', async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined);
    const api = makeFakeApi();
    render(<IdentityEditFull jumper={makeJumper()} onCancel={() => {}} onSaved={onSaved} api={api} />);

    fireEvent.click(screen.getByLabelText('Delete tandem rating'));
    await act(async () => {
      fireEvent.click(screen.getByText('Save'));
    });
    expect(api.deleteJumperTandemRating).toHaveBeenCalledTimes(1);
    expect(api.deleteJumperTandemRating.mock.calls[0]).toEqual(['jumper-1', 't1']);
    expect(api.updateJumperTandemRating).not.toHaveBeenCalled();
  });

  it('Add Tandem Rating issues a POST with default system=upt_sigma', async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined);
    const api = makeFakeApi();
    render(
      <IdentityEditFull
        jumper={makeJumper({ tandem_ratings: [] })}
        onCancel={() => {}}
        onSaved={onSaved}
        api={api}
      />,
    );

    fireEvent.click(screen.getByLabelText('Add Tandem Rating'));
    fireEvent.change(screen.getByLabelText('Tandem expiry date'), {
      target: { value: '2027-05-01' },
    });

    await act(async () => {
      fireEvent.click(screen.getByText('Save'));
    });
    expect(api.addJumperTandemRating).toHaveBeenCalledTimes(1);
    const [, body] = api.addJumperTandemRating.mock.calls[0];
    expect(body.system).toBe('upt_sigma');
    expect(body.expiry_date).toBe('2027-05-01');
    expect(body).not.toHaveProperty('id');
  });

  it("system='other' reveals the system_other input and persists its value", async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined);
    const api = makeFakeApi();
    render(
      <IdentityEditFull
        jumper={makeJumper({ tandem_ratings: [] })}
        onCancel={() => {}}
        onSaved={onSaved}
        api={api}
      />,
    );

    fireEvent.click(screen.getByLabelText('Add Tandem Rating'));
    fireEvent.change(screen.getByLabelText('Tandem system'), { target: { value: 'other' } });
    const otherInput = screen.getByLabelText('Tandem system name');
    fireEvent.change(otherInput, { target: { value: 'Sigma X-Class' } });
    fireEvent.change(screen.getByLabelText('Tandem expiry date'), {
      target: { value: '2027-06-01' },
    });

    await act(async () => {
      fireEvent.click(screen.getByText('Save'));
    });
    const [, body] = api.addJumperTandemRating.mock.calls[0];
    expect(body.system).toBe('other');
    expect(body.system_other).toBe('Sigma X-Class');
  });

  it('Add Medical issues a POST with kind=class_iii and the typed authority', async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined);
    const api = makeFakeApi();
    render(<IdentityEditFull jumper={makeJumper()} onCancel={() => {}} onSaved={onSaved} api={api} />);

    fireEvent.click(screen.getByLabelText('Add Medical'));
    fireEvent.change(screen.getByLabelText('Issuing authority'), {
      target: { value: 'Transport Canada' },
    });
    fireEvent.change(screen.getByLabelText('Medical expiry date'), {
      target: { value: '2027-10-15' },
    });

    await act(async () => {
      fireEvent.click(screen.getByText('Save'));
    });
    expect(api.addJumperMedical).toHaveBeenCalledTimes(1);
    const [, body] = api.addJumperMedical.mock.calls[0];
    expect(body.kind).toBe('class_iii');
    expect(body.issuing_authority).toBe('Transport Canada');
    expect(body.expiry_date).toBe('2027-10-15');
    expect(body).not.toHaveProperty('id');
  });

  it('editing currency_reset_at on a tandem rating PUTs the new value', async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined);
    const api = makeFakeApi();
    render(<IdentityEditFull jumper={makeJumper()} onCancel={() => {}} onSaved={onSaved} api={api} />);

    fireEvent.change(screen.getByLabelText('Tandem currency reset date'), {
      target: { value: '2026-03-15' },
    });
    await act(async () => {
      fireEvent.click(screen.getByText('Save'));
    });
    expect(api.updateJumperTandemRating).toHaveBeenCalledTimes(1);
    expect(api.updateJumperTandemRating.mock.calls[0][2].currency_reset_at).toBe('2026-03-15');
  });
});


// --------------------------------------------------------------------- //
// Partial-save UX (D56 Phase 5b)
// --------------------------------------------------------------------- //

describe('IdentityEditFull partial-save UX', () => {
  let confirmSpy;
  beforeEach(() => {
    confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
  });
  afterEach(() => {
    confirmSpy.mockRestore();
  });

  it('on partial failure: banner appears, onSaved is NOT called, form persists', async () => {
    const onSaved = vi.fn();
    const api = makeFakeApi();
    // Identity PUT succeeds, but the membership delete fails.
    // Trigger a membership delete by deleting it in the editor, plus
    // an identity tweak to dirty the form.
    api.deleteJumperMembership.mockRejectedValueOnce(
      Object.assign(new Error('http 422'), {
        problem: { title: 'Validation failed', detail: 'membership in use' },
      }),
    );
    render(<IdentityEditFull jumper={makeJumper()} onCancel={() => {}} onSaved={onSaved} api={api} />);

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Alex Renamed' } });
    fireEvent.click(screen.getByLabelText('Delete membership'));

    await act(async () => {
      fireEvent.click(screen.getByText('Save'));
    });

    // Banner is up.
    const banner = screen.getByRole('alert', { name: 'Partial save' });
    expect(banner).toBeInTheDocument();
    expect(banner.textContent).toContain('Save stopped at a failure');
    expect(banner.textContent).toContain('Removed membership');
    expect(banner.textContent).toContain('Validation failed');
    expect(banner.textContent).toContain('membership in use');
    // Identity put landed — it should appear in the completed list.
    expect(banner.textContent).toContain('Identity (name, exit weight)');
    // Form is not closed — onSaved not invoked.
    expect(onSaved).not.toHaveBeenCalled();
    // The Save button is now disabled (form is locked).
    expect(screen.getByText('Save').closest('button')).toBeDisabled();
  });

  it('Retry remaining re-runs only the unfinished tail and closes on full success', async () => {
    const onSaved = vi.fn();
    const api = makeFakeApi();
    // First attempt: identity OK, membership DELETE fails. Retry:
    // membership DELETE succeeds (and the rest of the empty diff
    // completes trivially).
    api.deleteJumperMembership.mockRejectedValueOnce(new Error('network'));
    render(<IdentityEditFull jumper={makeJumper()} onCancel={() => {}} onSaved={onSaved} api={api} />);

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Alex Renamed' } });
    fireEvent.click(screen.getByLabelText('Delete membership'));

    await act(async () => {
      fireEvent.click(screen.getByText('Save'));
    });

    // First attempt: updateJumper called once, deleteJumperMembership
    // called once (and rejected). onSaved still not invoked.
    expect(api.updateJumper).toHaveBeenCalledTimes(1);
    expect(api.deleteJumperMembership).toHaveBeenCalledTimes(1);
    expect(onSaved).not.toHaveBeenCalled();

    // Click Retry remaining.
    await act(async () => {
      fireEvent.click(screen.getByLabelText('Retry remaining'));
    });

    // Identity is NOT re-called (it already landed).
    expect(api.updateJumper).toHaveBeenCalledTimes(1);
    // Delete is retried (the queue still had it).
    expect(api.deleteJumperMembership).toHaveBeenCalledTimes(2);
    // onSaved invoked once the orchestrator finished cleanly.
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it("Close and reload calls onSaved with null (parent reloads)", async () => {
    const onSaved = vi.fn();
    const api = makeFakeApi();
    api.deleteJumperMembership.mockRejectedValueOnce(new Error('boom'));
    render(<IdentityEditFull jumper={makeJumper()} onCancel={() => {}} onSaved={onSaved} api={api} />);

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Alex Renamed' } });
    fireEvent.click(screen.getByLabelText('Delete membership'));

    await act(async () => {
      fireEvent.click(screen.getByText('Save'));
    });

    fireEvent.click(screen.getByText('Close and reload'));
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(onSaved.mock.calls[0][0]).toBeNull();
  });
});
