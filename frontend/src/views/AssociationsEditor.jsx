// AssociationsEditor — D56 Phase 3b.
//
// Editable counterpart to the read-only AssociationsSection. Drives
// every change through the staged-state mutation helpers
// (setRowField / addRow / removeRow) so the parent IdentityEditFull's
// Save handler can compute one diff and the Phase 5 orchestrator can
// sequence the writes per D56.
//
// Scope this slice covers:
//   - Edit member_number / expiry_date on existing memberships
//   - Edit level / issued_date on existing CoPs
//   - Edit code / expiry_date on existing ratings
//   - Add new memberships / CoPs / ratings inside an existing org card
//   - Add a fresh association (picks the org, seeds a blank membership)
//   - Delete any row (new rows splice out; persisted rows flip to
//     'deleted')
//
// Out of scope for this slice (follow-up sub-phase if needed):
//   - Editing notes / card_attachment_id (the attachment lazy-upload
//     flow needs save-orchestrator coordination — Phase 5)
//   - Editing org / org_other on a persisted row (delete and recreate
//     works today; in-place org change risks user confusion when the
//     row moves between cards mid-edit)
//
// The component is fully controlled by `staged` + `setStaged`. It
// holds only ephemeral UI state (the Add Association picker's open
// flag and its selected org).

import React, { useMemo, useState } from 'react';
import { Plus, Trash2, ChevronDown, ChevronRight } from 'lucide-react';
import {
  groupCredentialsByOrg,
  ORG_FULL_NAMES,
  CSPA_COP_LEVELS,
  USPA_COP_LEVELS,
  CSPA_RATING_CODES,
  USPA_RATING_CODES,
  ExpiryChip,
  CardChip,
  inputCls,
} from './Identity';
import { setRowField, addRow, removeRow } from './identityEditStaged';


// Build a `jumper`-shaped object out of the staged collections, with
// deleted rows filtered out, so groupCredentialsByOrg (which takes a
// jumper) can group the visible-to-editor rows. Bookkeeping fields
// remain on each row — the editor needs them.
function visibleJumperFromStaged(staged) {
  const live = (rows) => rows.filter((r) => r.status !== 'deleted');
  return {
    memberships: live(staged.memberships),
    cops: live(staged.cops),
    ratings: live(staged.ratings),
  };
}

// Local-only id for newly-added rows. The orchestrator strips it
// before POST (computeDiff's toBody drops `id` for creates), so the
// only consumer is React's reconciliation keying. crypto.randomUUID
// is available in modern browsers + Node 19+; the fallback is just
// for the most defensive case.
function newTmpId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return `tmp-${crypto.randomUUID()}`;
  }
  return `tmp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}


export default function AssociationsEditor({ staged, setStaged, jumper }) {
  const groups = useMemo(
    () => groupCredentialsByOrg(visibleJumperFromStaged(staged)),
    [staged],
  );

  function patchRow(coll, id, patch) {
    setStaged((s) => setRowField(s, coll, id, patch));
  }
  function delRow(coll, id) {
    setStaged((s) => removeRow(s, coll, id));
  }
  function appendRow(coll, seed) {
    setStaged((s) => addRow(s, coll, { ...seed, id: newTmpId() }));
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-[11px] tracking-[0.2em] text-neutral-300 font-medium">
          ASSOCIATIONS
        </h2>
        <AddAssociationButton
          onAdd={(org) => appendRow('memberships', {
            org,
            org_other: org === 'OTHER' ? '' : null,
            member_number: '',
            expiry_date: '',
            card_attachment_id: null,
            notes: null,
          })}
        />
      </div>

      {groups.length === 0 && (
        <div
          className="rounded-lg p-3 text-[12px] text-neutral-500"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border)' }}
        >
          No associations yet. Use the Add Association button to add a
          CSPA, USPA, or other federation membership.
        </div>
      )}

      <div className="space-y-2">
        {groups.map((group) => (
          <OrgCardEditor
            key={group.key}
            group={group}
            jumper={jumper}
            onPatch={patchRow}
            onDelete={delRow}
            onAppend={appendRow}
          />
        ))}
      </div>
    </div>
  );
}


// Add Association control. Inline-revealed dropdown rather than a
// modal — keeps the user inside the form's mental model.
function AddAssociationButton({ onAdd }) {
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState('CSPA');

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Add Association"
        className="px-3 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition hover:bg-neutral-800/70"
        style={{
          background: 'rgba(231,231,232,0.06)',
          color: 'var(--text)',
          border: '0.5px solid var(--border-strong)',
        }}
      >
        <Plus className="w-3 h-3" />
        Add Association
      </button>
    );
  }

  function commit() {
    onAdd(picked);
    setOpen(false);
    setPicked('CSPA');
  }
  function cancel() {
    setOpen(false);
    setPicked('CSPA');
  }

  return (
    <div className="flex items-center gap-2">
      <select
        value={picked}
        onChange={(e) => setPicked(e.target.value)}
        aria-label="Association org"
        className={`${inputCls} max-w-[280px]`}
      >
        <option value="CSPA">CSPA — Canadian Sport Parachuting</option>
        <option value="USPA">USPA — United States Parachute</option>
        <option value="OTHER">Other (free text)</option>
      </select>
      <button
        type="button"
        onClick={commit}
        aria-label="Confirm add association"
        className="px-2.5 py-1.5 rounded-md text-[11px] font-medium transition"
        style={{ background: 'var(--text)', color: 'var(--bg)' }}
      >
        Confirm
      </button>
      <button
        type="button"
        onClick={cancel}
        className="px-2.5 py-1.5 rounded-md text-[11px] font-medium text-neutral-400 hover:text-neutral-200 transition"
      >
        Cancel
      </button>
    </div>
  );
}


// One editable org card. Same chrome as the read-only OrgCard, but
// every row has live inputs + a delete button, and each column has
// an Add button at the bottom. New rows inherit the org from the
// card so the user can't accidentally file a credential under the
// wrong association.
function OrgCardEditor({ group, jumper, onPatch, onDelete, onAppend }) {
  const [expanded, setExpanded] = useState(true);

  const orgLabel = group.org === 'OTHER'
    ? (group.org_other || 'Other federation')
    : group.org;
  const orgFullName = group.org === 'OTHER'
    ? null
    : ORG_FULL_NAMES[group.org];

  const summary = [];
  if (group.memberships.length === 0) summary.push({ text: 'no membership', warn: true });
  if (group.cops.length > 0) summary.push({ text: `${group.cops.length} CoP${group.cops.length === 1 ? '' : 's'}` });
  if (group.ratings.length > 0) summary.push({ text: `${group.ratings.length} rating${group.ratings.length === 1 ? '' : 's'}` });

  const seedForOrg = (base) => ({
    org: group.org,
    org_other: group.org === 'OTHER' ? (group.org_other || '') : null,
    card_attachment_id: null,
    notes: null,
    ...base,
  });

  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border)' }}
    >
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-3 flex items-center justify-between hover:bg-neutral-800/30 transition text-left"
      >
        <div className="flex items-center gap-3 min-w-0">
          {expanded
            ? <ChevronDown className="w-3.5 h-3.5 text-neutral-500 flex-shrink-0" />
            : <ChevronRight className="w-3.5 h-3.5 text-neutral-500 flex-shrink-0" />}
          <span className="text-[14px] font-medium text-neutral-100">{orgLabel}</span>
          {orgFullName && (
            <span className="text-[11px] text-neutral-500 truncate">{orgFullName}</span>
          )}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {summary.map((s, i) => (
            <React.Fragment key={i}>
              {i > 0 && <span className="text-[10px] text-neutral-700">·</span>}
              <span
                className="text-[10px]"
                style={{ color: s.warn ? 'var(--status-watch)' : 'var(--text-faint)' }}
              >
                {s.text}
              </span>
            </React.Fragment>
          ))}
        </div>
      </button>

      {expanded && (
        <div className="px-4 py-3 space-y-3" style={{ borderTop: '0.5px solid var(--border)' }}>
          {group.memberships.map((m) => (
            <MembershipRowEditor
              key={m.id}
              row={m}
              jumper={jumper}
              onChange={(patch) => onPatch('memberships', m.id, patch)}
              onDelete={() => onDelete('memberships', m.id)}
            />
          ))}
          {group.memberships.length === 0 && (
            <div
              className="flex items-center justify-between gap-2 py-2 px-3 rounded-md text-[12px]"
              style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}
            >
              <span>No active membership for this association</span>
              <SmallAddButton
                label="Add Membership"
                onClick={() => onAppend('memberships', seedForOrg({
                  member_number: '',
                  expiry_date: '',
                }))}
              />
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-1">
            <div className="space-y-1.5">
              <div className="text-[9px] tracking-[0.15em] text-neutral-600 font-medium">
                COPS
              </div>
              {group.cops.map((c) => (
                <CopRowEditor
                  key={c.id}
                  row={c}
                  org={group.org}
                  jumper={jumper}
                  onChange={(patch) => onPatch('cops', c.id, patch)}
                  onDelete={() => onDelete('cops', c.id)}
                />
              ))}
              <SmallAddButton
                label="Add CoP"
                onClick={() => onAppend('cops', seedForOrg({
                  level: '',
                  issued_date: '',
                }))}
              />
            </div>
            <div className="space-y-1.5">
              <div className="text-[9px] tracking-[0.15em] text-neutral-600 font-medium">
                RATINGS
              </div>
              {group.ratings.map((r) => (
                <RatingRowEditor
                  key={r.id}
                  row={r}
                  org={group.org}
                  jumper={jumper}
                  onChange={(patch) => onPatch('ratings', r.id, patch)}
                  onDelete={() => onDelete('ratings', r.id)}
                />
              ))}
              <SmallAddButton
                label="Add Rating"
                onClick={() => onAppend('ratings', seedForOrg({
                  code: '',
                  expiry_date: '',
                }))}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


// Membership row — promoted styling (matches the read view).
function MembershipRowEditor({ row, jumper, onChange, onDelete }) {
  return (
    <div
      className="grid grid-cols-[1fr_auto_auto] items-center gap-2 py-2 px-3 rounded-md"
      style={{ background: 'var(--surface-2)' }}
    >
      <div className="flex items-center gap-3 flex-wrap min-w-0">
        {row.org === 'OTHER' && (
          <input
            value={row.org_other || ''}
            onChange={(e) => onChange({ org_other: e.target.value })}
            placeholder="federation name"
            maxLength={120}
            aria-label="Federation name"
            className={`${inputCls} max-w-[200px]`}
          />
        )}
        <input
          value={row.member_number || ''}
          onChange={(e) => onChange({ member_number: e.target.value })}
          placeholder="member #"
          maxLength={40}
          aria-label="Member number"
          className={`${inputCls} max-w-[160px] font-mono`}
        />
        <input
          type="date"
          value={row.expiry_date || ''}
          onChange={(e) => onChange({ expiry_date: e.target.value })}
          aria-label="Expiry date"
          className={`${inputCls} max-w-[160px]`}
        />
        {row.expiry_date && <ExpiryChip date={row.expiry_date} />}
        {row.card_attachment_id && <CardChip jumper={jumper} attachmentId={row.card_attachment_id} />}
      </div>
      <RowStatusChip status={row.status} />
      <DeleteRowButton onClick={onDelete} aria="Delete membership" />
    </div>
  );
}


function CopRowEditor({ row, org, jumper, onChange, onDelete }) {
  const options = org === 'CSPA' ? CSPA_COP_LEVELS
    : org === 'USPA' ? USPA_COP_LEVELS
      : null;
  return (
    <div
      className="grid grid-cols-[1fr_auto_auto] items-center gap-2 py-1.5 px-2 rounded"
      style={{ background: 'var(--surface-1)' }}
    >
      <div className="flex items-center gap-2 flex-wrap min-w-0">
        {options ? (
          <select
            value={row.level || ''}
            onChange={(e) => onChange({ level: e.target.value })}
            aria-label="CoP level"
            className={`${inputCls} max-w-[160px]`}
          >
            <option value="">—</option>
            {options.map(([v, label]) => (
              <option key={v} value={v}>{label}</option>
            ))}
          </select>
        ) : (
          <input
            value={row.level || ''}
            onChange={(e) => onChange({ level: e.target.value })}
            placeholder="level"
            aria-label="CoP level"
            className={`${inputCls} max-w-[160px]`}
          />
        )}
        <input
          type="date"
          value={row.issued_date || ''}
          onChange={(e) => onChange({ issued_date: e.target.value })}
          aria-label="Issued date"
          className={`${inputCls} max-w-[160px]`}
        />
        {row.card_attachment_id && <CardChip jumper={jumper} attachmentId={row.card_attachment_id} />}
      </div>
      <RowStatusChip status={row.status} />
      <DeleteRowButton onClick={onDelete} aria="Delete CoP" />
    </div>
  );
}


function RatingRowEditor({ row, org, jumper, onChange, onDelete }) {
  const options = org === 'CSPA' ? CSPA_RATING_CODES
    : org === 'USPA' ? USPA_RATING_CODES
      : null;
  return (
    <div
      className="grid grid-cols-[1fr_auto_auto] items-center gap-2 py-1.5 px-2 rounded"
      style={{ background: 'var(--surface-1)' }}
    >
      <div className="flex items-center gap-2 flex-wrap min-w-0">
        {options ? (
          <select
            value={row.code || ''}
            onChange={(e) => onChange({ code: e.target.value })}
            aria-label="Rating code"
            className={`${inputCls} max-w-[200px]`}
          >
            <option value="">—</option>
            {options.map(([v, label]) => (
              <option key={v} value={v}>{label}</option>
            ))}
          </select>
        ) : (
          <input
            value={row.code || ''}
            onChange={(e) => onChange({ code: e.target.value })}
            placeholder="rating"
            aria-label="Rating code"
            className={`${inputCls} max-w-[200px]`}
          />
        )}
        <input
          type="date"
          value={row.expiry_date || ''}
          onChange={(e) => onChange({ expiry_date: e.target.value })}
          aria-label="Expiry date"
          className={`${inputCls} max-w-[160px]`}
        />
        {row.expiry_date && <ExpiryChip date={row.expiry_date} />}
        {row.card_attachment_id && <CardChip jumper={jumper} attachmentId={row.card_attachment_id} />}
      </div>
      <RowStatusChip status={row.status} />
      <DeleteRowButton onClick={onDelete} aria="Delete rating" />
    </div>
  );
}


// Small visual indicator that a row is staged-but-not-persisted. The
// orchestrator (Phase 5) will use the underlying status for sequencing;
// the chip is purely for the user's awareness — same EDITING-yellow
// language the parent form's header uses, so the user sees a cohesive
// "this is unsaved" cue across the whole form.
function RowStatusChip({ status }) {
  if (status === 'unchanged') return <span aria-hidden className="w-0" />;
  let label, style;
  if (status === 'new') {
    label = 'NEW';
    style = { color: 'var(--status-ready)', background: 'rgba(134,239,172,0.10)', border: '0.5px solid rgba(134,239,172,0.30)' };
  } else if (status === 'modified') {
    label = 'EDITED';
    style = { color: 'var(--status-watch)', background: 'rgba(251,191,36,0.10)', border: '0.5px solid rgba(251,191,36,0.30)' };
  } else {
    return <span aria-hidden className="w-0" />;
  }
  return (
    <span
      className="text-[9px] tracking-[0.15em] px-1.5 py-0.5 rounded-full flex-shrink-0"
      style={style}
    >
      {label}
    </span>
  );
}


function DeleteRowButton({ onClick, aria }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={aria}
      className="p-1.5 rounded text-neutral-400 hover:text-red-300 hover:bg-red-900/30 transition flex-shrink-0"
    >
      <Trash2 className="w-3.5 h-3.5" />
    </button>
  );
}


function SmallAddButton({ onClick, label }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="px-2 py-1 rounded text-[10px] font-medium flex items-center gap-1 transition hover:bg-neutral-800/50"
      style={{
        background: 'transparent',
        color: 'var(--text-muted)',
        border: '0.5px solid var(--border-strong)',
      }}
    >
      <Plus className="w-2.5 h-2.5" />
      {label}
    </button>
  );
}
