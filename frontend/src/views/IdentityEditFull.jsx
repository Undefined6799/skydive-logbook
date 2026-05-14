// IdentityEditFull — unified Profile edit form (D56).
//
// State as of Phase 5: feature-complete. Identity fields plus all
// five D47 credential collections (memberships, CoPs, ratings,
// tandem ratings, medicals) are editable through staged local state.
// Save sequences the writes through identityEditOrchestrator (DELETE
// → PUT → POST per D56) and reports partial-save failures inline
// with a Retry remaining affordance.
//
// Phase 5 also flips Profile.jsx's Edit button to this component —
// the legacy IdentityEdit (name + exit weight only) is gone.
//
// `api` defaults to the real `../api` module; tests inject a fake to
// observe call sequences and force specific failures.

import React, { useState, useMemo } from 'react';
import { Loader2, Save, AlertTriangle, CheckCircle2 } from 'lucide-react';
import * as defaultApi from '../api';
import {
  Field,
  FormGrid,
  inputCls,
} from './Profile';
import {
  initStagedJumper,
  computeIsDirty,
  computeDiff,
  diffIsEmpty,
} from './identityEditStaged';
import {
  runOrchestrator,
  buildOrchestratorApi,
  isFullSuccess,
  describeOp,
  describeFailure,
} from './identityEditOrchestrator';
import AssociationsEditor from './AssociationsEditor';
import TandemRatingsEditor from './TandemRatingsEditor';
import MedicalsEditor from './MedicalsEditor';


export default function IdentityEditFull({ jumper, onCancel, onSaved, api = defaultApi }) {
  const [staged, setStaged] = useState(() => initStagedJumper(jumper));
  const [saving, setSaving] = useState(false);
  // Partial-save state: null = idle (or last save fully succeeded),
  // otherwise { completed, failed, remaining }. While non-null, the
  // form inputs are disabled and the banner offers Retry / Close.
  const [partial, setPartial] = useState(null);

  const orchestratorApi = useMemo(() => buildOrchestratorApi(api), [api]);

  const dirty = useMemo(
    () => computeIsDirty(staged, jumper),
    [staged, jumper],
  );

  function setName(value) {
    setStaged((s) => ({ ...s, identity: { ...s.identity, name: value } }));
  }
  function setExitWeight(value) {
    setStaged((s) => ({
      ...s,
      identity: { ...s.identity, exit_weight_lb: value },
    }));
  }

  function handleCancel() {
    // Cancel-confirm only when there are unsaved edits — clicking
    // Edit then Cancel without typing leaves the read view alone.
    if (dirty && !window.confirm('Discard your changes?')) return;
    if (onCancel) onCancel();
  }

  // Runs the orchestrator with `diff`, sets partial-failure state on
  // a non-full-success result, fires onSaved on full success. Shared
  // between the initial Save submit and the Retry button.
  async function runSave(diff) {
    setSaving(true);
    try {
      const result = await runOrchestrator({
        jumperId: jumper.id,
        diff,
        api: orchestratorApi,
      });
      if (isFullSuccess(result)) {
        setPartial(null);
        if (onSaved) await onSaved(result);
      } else {
        setPartial(result);
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleSave(e) {
    e?.preventDefault?.();
    const diff = computeDiff(staged, jumper);
    if (diffIsEmpty(diff)) {
      // Nothing to send — equivalent to Cancel without confirmation.
      if (onCancel) onCancel();
      return;
    }
    await runSave(diff);
  }

  async function handleRetry() {
    if (!partial || !partial.remaining) return;
    await runSave(partial.remaining);
  }

  function handleCloseAfterPartial() {
    // The user accepts the partially-saved state. Tell the parent so
    // it reloads the jumper from the server (which now reflects the
    // ops that did land). Staged remaining changes are dropped.
    setPartial(null);
    if (onSaved) onSaved(null);
  }

  // The Save button is enabled when there is at least one staged
  // change and the exit-weight reading is a positive number (matching
  // the legacy IdentityEdit's guard). Phase 5 may relax this once the
  // orchestrator surfaces per-field validation errors inline.
  const exitWeightNum = parseFloat(staged.identity.exit_weight_lb);
  const exitWeightValid = !Number.isNaN(exitWeightNum) && exitWeightNum > 0;
  const canSave = dirty && exitWeightValid && !saving;

  return (
    <form
      onSubmit={handleSave}
      className="rounded-xl p-5 mb-6"
      style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)' }}
    >
      <div className="flex items-center justify-between mb-4">
        <div className="text-[10px] tracking-[0.25em] text-neutral-500 font-medium">
          EDIT IDENTITY
        </div>
        <span
          className="text-[9px] tracking-[0.15em] px-2 py-0.5 rounded-full"
          style={{
            color: 'var(--status-watch)',
            background: 'rgba(251,191,36,0.08)',
            border: '0.5px solid rgba(251,191,36,0.25)',
          }}
        >
          EDITING
        </span>
      </div>

      <FormGrid>
        <Field label="NAME">
          <input
            value={staged.identity.name}
            onChange={(e) => setName(e.target.value)}
            disabled={saving}
            placeholder="optional display name"
            className={inputCls}
            aria-label="Name"
          />
        </Field>
        <Field label="EXIT WEIGHT (lb)">
          <input
            type="number"
            step="0.1"
            min="0.1"
            value={staged.identity.exit_weight_lb}
            onChange={(e) => setExitWeight(e.target.value)}
            disabled={saving}
            placeholder="all-up exit weight"
            className={inputCls}
            aria-label="Exit weight"
          />
        </Field>
      </FormGrid>

      <div className="text-[11px] text-neutral-500 mt-3 leading-relaxed">
        Saving stamps today as the last-confirmed date, even if the weight
        didn't change. Your exit weight feeds lineset-wear calculations,
        so changes here propagate to your linesets' remaining-budget
        estimates.
      </div>

      <div className="my-5 border-t" style={{ borderColor: 'var(--border-strong)' }} />

      <AssociationsEditor staged={staged} setStaged={setStaged} jumper={jumper} />

      <div className="my-5 border-t" style={{ borderColor: 'var(--border-strong)' }} />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <TandemRatingsEditor staged={staged} setStaged={setStaged} jumper={jumper} />
        <MedicalsEditor staged={staged} setStaged={setStaged} jumper={jumper} />
      </div>

      {partial && (
        <PartialSaveBanner
          partial={partial}
          saving={saving}
          onRetry={handleRetry}
          onClose={handleCloseAfterPartial}
        />
      )}

      <div className="flex items-center justify-end gap-2 mt-5">
        <button
          type="button"
          onClick={handleCancel}
          disabled={saving || !!partial}
          className="px-3 py-1.5 text-[12px] text-neutral-400 transition hover:text-neutral-200 disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={!canSave || !!partial}
          className="px-3.5 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition"
          style={{
            background: saving ? 'var(--surface-3)' : 'var(--text)',
            color: saving ? 'var(--text-faint)' : 'var(--bg)',
            cursor: saving ? 'not-allowed' : 'pointer',
            opacity: canSave && !partial ? 1 : 0.5,
          }}
        >
          {saving ? (
            <>
              <Loader2 className="w-3 h-3 animate-spin" />
              Saving…
            </>
          ) : (
            <>
              <Save className="w-3 h-3" />
              Save
            </>
          )}
        </button>
      </div>
    </form>
  );
}


// Partial-save UI per D56. Shows what landed (green check list),
// which call stopped the run (RFC 9457 problem+json detail when the
// error is an ApiError), and a Retry remaining / Close pair of
// actions. The form's inputs and Save button are disabled while this
// banner is shown — retry on stale staged data would be confusing,
// so we ask the user to either retry the captured `remaining` or
// close and reload.
function PartialSaveBanner({ partial, saving, onRetry, onClose }) {
  const failureLine = describeFailure(partial.failed);
  return (
    <div
      role="alert"
      aria-label="Partial save"
      className="mt-5 p-4 rounded-lg"
      style={{
        background: 'rgba(248,113,113,0.05)',
        border: '0.5px solid rgba(248,113,113,0.30)',
      }}
    >
      <div className="flex items-start gap-2 mb-2">
        <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: 'var(--status-critical)' }} />
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-medium text-neutral-100">
            Save stopped at a failure
          </div>
          <div className="text-[12px] text-neutral-400 mt-1 break-words">
            {failureLine}
          </div>
        </div>
      </div>

      {partial.completed.length > 0 && (
        <div className="mt-3">
          <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium mb-1.5">
            COMPLETED ({partial.completed.length})
          </div>
          <ul className="space-y-0.5">
            {partial.completed.map((op, i) => (
              <li
                key={i}
                className="text-[12px] text-neutral-300 flex items-center gap-1.5"
              >
                <CheckCircle2 className="w-3 h-3 flex-shrink-0" style={{ color: 'var(--status-ready)' }} />
                {describeOp(op)}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex items-center justify-end gap-2 mt-4">
        <button
          type="button"
          onClick={onClose}
          disabled={saving}
          className="px-3 py-1.5 text-[12px] text-neutral-400 transition hover:text-neutral-200 disabled:opacity-50"
        >
          Close and reload
        </button>
        <button
          type="button"
          onClick={onRetry}
          disabled={saving}
          aria-label="Retry remaining"
          className="px-3.5 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition"
          style={{
            background: saving ? 'var(--surface-3)' : 'var(--text)',
            color: saving ? 'var(--text-faint)' : 'var(--bg)',
            cursor: saving ? 'not-allowed' : 'pointer',
          }}
        >
          {saving ? (
            <>
              <Loader2 className="w-3 h-3 animate-spin" />
              Retrying…
            </>
          ) : (
            'Retry remaining'
          )}
        </button>
      </div>
    </div>
  );
}


// Phase 3a had a projectStagedToJumper here for the read-only display
// components. Phase 4 swapped those for editable components driven
// directly by `staged`, so the projection became dead code and was
// removed. If a future sub-phase needs to render a read-only preview
// of staged state, reintroduce a projection helper at that point —
// the git history of this file carries the pattern.
