// TandemRatingsEditor — D56 Phase 4.
//
// Editable counterpart to CompactTandemRatings. Drives every change
// through the staged-state mutation helpers so the parent form's
// single Save can compute one diff for the Phase 5 orchestrator.
//
// Scope this slice covers:
//   - Edit system / system_other / expiry_date / currency_reset_at
//     on existing tandem ratings
//   - Add a new tandem rating (system pre-set to UPT Sigma — the most
//     common in CA where D47 originates; user changes it if needed)
//   - Delete any row
//
// Out of scope (deferred):
//   - Editing notes / card_attachment_id (attachment editing lands
//     after the Phase 5 orchestrator settles; same posture as the
//     associations editor)
//
// `currency_reset_at` is D47's manual override the user sets after a
// supervised re-currency jump. We surface it as a date input next to
// expiry — it is intentionally separate (not a derived field) so the
// orchestrator's PUT/POST writes whatever the user typed.

import React from 'react';
import { Plus, Trash2 } from 'lucide-react';
import {
  TANDEM_SYSTEMS,
  ExpiryChip,
  CardChip,
  inputCls,
} from './Identity';
import { setRowField, addRow, removeRow } from './identityEditStaged';


function newTmpId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return `tmp-${crypto.randomUUID()}`;
  }
  return `tmp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}


export default function TandemRatingsEditor({ staged, setStaged, jumper }) {
  const visible = staged.tandem_ratings.filter((r) => r.status !== 'deleted');

  function patch(id, p) {
    setStaged((s) => setRowField(s, 'tandem_ratings', id, p));
  }
  function del(id) {
    setStaged((s) => removeRow(s, 'tandem_ratings', id));
  }
  function add() {
    setStaged((s) => addRow(s, 'tandem_ratings', {
      id: newTmpId(),
      system: 'upt_sigma',
      system_other: null,
      expiry_date: '',
      card_attachment_id: null,
      currency_reset_at: null,
      notes: null,
    }));
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-[11px] tracking-[0.2em] text-neutral-300 font-medium">
          TANDEM RATINGS
        </h2>
        <button
          type="button"
          onClick={add}
          aria-label="Add Tandem Rating"
          className="px-2 py-1 rounded text-[10px] font-medium flex items-center gap-1 transition hover:bg-neutral-800/50"
          style={{
            background: 'transparent',
            color: 'var(--text-muted)',
            border: '0.5px solid var(--border-strong)',
          }}
        >
          <Plus className="w-2.5 h-2.5" />
          Add
        </button>
      </div>

      {visible.length === 0 && (
        <div className="text-[11px] text-neutral-500 px-2 py-2">
          None recorded.
        </div>
      )}

      <div className="space-y-1.5">
        {visible.map((row) => (
          <TandemRatingRowEditor
            key={row.id}
            row={row}
            jumper={jumper}
            onChange={(p) => patch(row.id, p)}
            onDelete={() => del(row.id)}
          />
        ))}
      </div>
    </div>
  );
}


function TandemRatingRowEditor({ row, jumper, onChange, onDelete }) {
  const isOther = row.system === 'other';
  return (
    <div
      className="grid grid-cols-[1fr_auto_auto] items-center gap-2 py-1.5 px-2 rounded"
      style={{ background: 'var(--surface-1)' }}
    >
      <div className="flex items-center gap-2 flex-wrap min-w-0">
        <select
          value={row.system || ''}
          onChange={(e) => onChange({
            system: e.target.value,
            // Drop system_other when switching back to a known system,
            // so the orchestrator doesn't ship a stale value alongside.
            system_other: e.target.value === 'other' ? (row.system_other || '') : null,
          })}
          aria-label="Tandem system"
          className={`${inputCls} max-w-[200px]`}
        >
          {TANDEM_SYSTEMS.map(([v, label]) => (
            <option key={v} value={v}>{label}</option>
          ))}
        </select>
        {isOther && (
          <input
            value={row.system_other || ''}
            onChange={(e) => onChange({ system_other: e.target.value })}
            placeholder="system name"
            maxLength={120}
            aria-label="Tandem system name"
            className={`${inputCls} max-w-[200px]`}
          />
        )}
        <input
          type="date"
          value={row.expiry_date || ''}
          onChange={(e) => onChange({ expiry_date: e.target.value })}
          aria-label="Tandem expiry date"
          className={`${inputCls} max-w-[150px]`}
        />
        {row.expiry_date && <ExpiryChip date={row.expiry_date} />}
        <label className="text-[10px] text-neutral-500 flex items-center gap-1.5">
          currency reset
          <input
            type="date"
            value={row.currency_reset_at || ''}
            onChange={(e) => onChange({
              currency_reset_at: e.target.value || null,
            })}
            aria-label="Tandem currency reset date"
            className={`${inputCls} max-w-[140px]`}
          />
        </label>
        {row.card_attachment_id && <CardChip jumper={jumper} attachmentId={row.card_attachment_id} />}
      </div>
      <RowStatusChip status={row.status} />
      <DeleteRowButton onClick={onDelete} aria="Delete tandem rating" />
    </div>
  );
}


function RowStatusChip({ status }) {
  if (status === 'unchanged' || status === 'deleted') {
    return <span aria-hidden className="w-0" />;
  }
  const isNew = status === 'new';
  const style = isNew
    ? { color: 'var(--status-ready)', background: 'rgba(134,239,172,0.10)', border: '0.5px solid rgba(134,239,172,0.30)' }
    : { color: 'var(--status-watch)', background: 'rgba(251,191,36,0.10)', border: '0.5px solid rgba(251,191,36,0.30)' };
  return (
    <span
      className="text-[9px] tracking-[0.15em] px-1.5 py-0.5 rounded-full flex-shrink-0"
      style={style}
    >
      {isNew ? 'NEW' : 'EDITED'}
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
