import React, { useEffect, useState } from 'react';
import { X, Save, Loader2, AlertTriangle } from 'lucide-react';
import { getAad, updateAad, ApiError } from '../api';

// Small focused modal for changing an AAD's mode (Pro / Expert /
// Tandem / etc.). Only opened when the AAD's
// ``is_changeable_mode`` flag is True; the rig header gates the
// affordance on that flag so the user can't fire this modal for a
// fixed-mode AAD.
//
// Mode is free text on the wire — different manufacturers ship
// different mode names (Cypres 2: Pro/Expert/Tandem; Vigil 2:
// Pro/Student/Tandem; M2: Pro/Student). The modal renders a free
// input with a hint string rather than a fixed dropdown so any
// supported manufacturer just works.
//
// PUT semantics: full-replace per the AAD model contract. We fetch
// the current record on open, mutate ``mode``, and send the whole
// thing back. That preserves ``assigned_rig_id``, counter
// initials, DOM, etc. server-side.

export default function EditAadModeModal({ visible, aadId, onClose, onSaved }) {
  const [aad, setAad] = useState(null);
  const [mode, setMode] = useState('');
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!visible) {
      document.body.style.overflow = '';
      return;
    }
    document.body.style.overflow = 'hidden';
    setError(null);
    setSubmitting(false);
    if (!aadId) return;
    let cancelled = false;
    setLoading(true);
    getAad(aadId)
      .then((a) => {
        if (cancelled) return;
        setAad(a);
        setMode(a.mode || '');
      })
      .catch((err) => { if (!cancelled) setError(err); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => {
      cancelled = true;
      document.body.style.overflow = '';
    };
  }, [visible, aadId]);

  if (!visible) return null;

  const trimmed = mode.trim();
  const canSave = Boolean(aad && trimmed !== (aad.mode || '').trim());

  async function handleSave() {
    if (!aad || !canSave || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      // Full replace per AAD model. Pass every editable field
      // verbatim from the on-disk record; only ``mode`` differs.
      // ``status``, ``assigned_rig_id``, counters, etc. are
      // server-controlled and round-tripped untouched.
      const payload = {
        status: aad.status,
        manufacturer: aad.manufacturer,
        model: aad.model,
        serial: aad.serial,
        date_of_manufacture: aad.date_of_manufacture,
        mode: trimmed || null,
        is_changeable_mode: aad.is_changeable_mode,
        jump_count_initial: aad.jump_count_initial,
        fire_count_initial: aad.fire_count_initial,
      };
      const updated = await updateAad(aad.id, payload);
      if (onSaved) onSaved(updated);
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  // Manufacturer-specific mode hints. Free text on the wire, but a
  // suggestion goes a long way for the user who's not sure what
  // their AAD's mode label is called. Falls back to a generic
  // hint when the manufacturer is missing or unrecognised.
  const hint = modeHint(aad?.manufacturer, aad?.model);

  return (
    <>
      <div
        onClick={submitting ? undefined : onClose}
        className="fixed inset-0 z-40"
        style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)' }}
      />
      <div className="fixed inset-0 z-50 flex items-start justify-center p-6 pointer-events-none">
        <div
          onClick={(e) => e.stopPropagation()}
          className="rounded-2xl w-full max-w-sm pointer-events-auto mt-20"
          style={{
            background: 'var(--surface-1)',
            border: '0.5px solid var(--border-strong)',
          }}
        >
          <div
            className="flex items-start justify-between px-5 pt-5 pb-3.5"
            style={{ borderBottom: '0.5px solid var(--border-strong)' }}
          >
            <div>
              <div className="text-[9px] tracking-[0.25em] text-neutral-500 font-medium mb-1">
                CHANGE AAD MODE
              </div>
              <div className="text-[17px] font-medium tracking-tight">
                {aad ? `${aad.manufacturer || 'AAD'} ${aad.model || ''}`.trim() : 'AAD mode'}
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="w-8 h-8 rounded-lg flex items-center justify-center transition hover:bg-neutral-800 disabled:opacity-50"
              style={{ background: 'var(--surface-2)' }}
              aria-label="Close"
            >
              <X className="w-3.5 h-3.5 text-neutral-400" />
            </button>
          </div>

          {error && <ErrorBanner error={error} />}

          <div className="p-5">
            {loading && (
              <div className="flex items-center gap-2 text-[12px] text-neutral-500">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                Loading AAD…
              </div>
            )}

            {!loading && aad && (
              <>
                <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium mb-1.5">
                  MODE
                </div>
                <input
                  type="text"
                  value={mode}
                  onChange={(e) => setMode(e.target.value)}
                  disabled={submitting}
                  placeholder={hint}
                  className="w-full rounded-md px-3 py-1.5 text-[13px] text-neutral-100 bg-[var(--bg)] border border-[var(--border-strong)] focus:border-[#3a3d41] transition outline-none disabled:opacity-50"
                />
                <div className="text-[10px] text-neutral-500 mt-2 leading-relaxed">
                  {hint
                    ? `Typical values for this device: ${hint}.`
                    : 'Free text — match the label printed on the unit.'}
                </div>
              </>
            )}
          </div>

          <div
            className="flex items-center gap-2 px-5 py-3"
            style={{
              borderTop: '0.5px solid var(--border-strong)',
            }}
          >
            <div className="flex-1" />
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="px-3 py-1.5 text-[12px] text-neutral-400 transition hover:text-neutral-200 disabled:opacity-40"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={!canSave || submitting}
              className="px-3.5 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition disabled:opacity-50"
              style={{
                background: canSave && !submitting ? 'var(--text)' : 'var(--surface-3)',
                color: canSave && !submitting ? 'var(--bg)' : 'var(--text-faint)',
                cursor: canSave && !submitting ? 'pointer' : 'not-allowed',
              }}
            >
              {submitting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
              Save mode
            </button>
          </div>
        </div>
      </div>
    </>
  );
}


function modeHint(manufacturer, model) {
  const m = `${manufacturer || ''} ${model || ''}`.toLowerCase();
  if (m.includes('cypres')) return 'Pro / Expert / Tandem / Student';
  if (m.includes('vigil')) return 'Pro / Student / Tandem';
  if (m.includes('m2') || m.includes('mars')) return 'Pro / Student';
  return '';
}


function ErrorBanner({ error }) {
  let message = String(error.message || error);
  let pointers = [];
  if (error instanceof ApiError && error.problem) {
    message = error.problem.detail || message;
    if (Array.isArray(error.problem.errors)) {
      pointers = error.problem.errors;
    }
  }
  return (
    <div
      className="px-5 py-2.5 text-[12px]"
      style={{
        background: 'rgba(248,113,113,0.06)',
        color: 'var(--status-critical)',
        borderBottom: '0.5px solid var(--border-strong)',
      }}
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className="w-3.5 h-3.5 mt-0.5" />
        <div className="flex-1 min-w-0">
          <div>{message}</div>
          {pointers.map((p, i) => (
            <div key={i} className="text-[11px] text-neutral-400 mt-0.5">
              <span className="font-mono">{p.pointer}</span>: {p.detail}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
