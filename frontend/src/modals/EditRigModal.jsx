import React, { useEffect, useState } from 'react';
import { X, Save, Loader2, AlertTriangle } from 'lucide-react';
import { getRig, updateRig, ApiError } from '../api';

// Edit a rig's metadata — nickname + jurisdiction.
//
// Scope-limited on purpose:
//
//   * **Components** stay read-only here. D37 forbids changing the
//     four ``current_*_id`` fields via plain PUT; mutations go
//     through the dedicated swap endpoints (currently swap_main;
//     swap_reserve / swap_aad / swap_container land additively).
//     The modal still SENDS the existing four ids back in the
//     payload because RigUpdate requires them — but they match
//     the on-disk values, so the service merge is a no-op for
//     those fields.
//   * **Repack history** is R.5 territory (D38) and lives outside
//     this modal.
//   * **Display order**, **starred**, **notes_log** are service-
//     controlled.
//
// The modal fetches the rig fresh on open (the parent passes only
// the id) so a concurrent edit from another window doesn't cause
// us to PUT stale ids.
//
// On save: PUT /api/v1/rigs/{id} with the new nickname +
// jurisdiction and the existing component refs. The service-layer
// folder rename (D4 sanitize-and-rename) happens automatically
// when nickname changes.

const JURISDICTION_BUTTONS = [
  { label: 'USPA', value: 'USPA' },
  { label: 'CSPA', value: 'CSPA' },
  { label: 'Both', value: 'both' },
];


// Pull the most recent repack date from the rig's history, or ''
// when the history is empty (a rig that was created without a
// repack seed). Used to pre-fill the date input on open.
function latestRepackDate(rig) {
  if (!rig?.repack_history?.length) return '';
  const last = rig.repack_history[rig.repack_history.length - 1];
  return last?.date || '';
}


export default function EditRigModal({ visible, rigId, onClose, onSaved }) {
  const [rig, setRig] = useState(null);
  const [nickname, setNickname] = useState('');
  const [jurisdiction, setJurisdiction] = useState('USPA');
  const [lastRepackDate, setLastRepackDate] = useState('');
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  // Body-scroll lock + reset state on every open. Refetch the rig
  // each time the modal opens so we always PUT the fresh ids
  // (handles the rare case where another window swapped a
  // component while this modal was closed).
  useEffect(() => {
    if (!visible) {
      document.body.style.overflow = '';
      return;
    }
    document.body.style.overflow = 'hidden';
    setError(null);
    setSubmitting(false);
    if (!rigId) return;
    let cancelled = false;
    setLoading(true);
    getRig(rigId)
      .then((r) => {
        if (cancelled) return;
        setRig(r);
        setNickname(r.nickname);
        setJurisdiction(r.jurisdiction);
        setLastRepackDate(latestRepackDate(r));
      })
      .catch((err) => { if (!cancelled) setError(err); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => {
      cancelled = true;
      document.body.style.overflow = '';
    };
  }, [visible, rigId]);

  if (!visible) return null;

  const origDate = latestRepackDate(rig);
  const canSave = Boolean(
    rig
    && nickname.trim().length > 0
    && (
      nickname !== rig.nickname
      || jurisdiction !== rig.jurisdiction
      || lastRepackDate !== origDate
    ),
  );

  async function handleSave() {
    if (!rig || !canSave || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      // RigUpdate requires the four component ids — re-send the
      // on-disk values verbatim so the service merge keeps them
      // unchanged. Sending different ids would 409 per D37.
      const payload = {
        nickname: nickname.trim(),
        jurisdiction,
        current_container_id: rig.current_container_id,
        current_main_id: rig.current_main_id,
        current_reserve_id: rig.current_reserve_id,
        current_aad_id: rig.current_aad_id,
        notes_log: rig.notes_log || [],
      };

      // D66 ``repack_history`` resolution. Three cases:
      //   1. User didn't change the date  → omit the field;
      //      service-side D66 logic preserves the on-disk list.
      //   2. User changed the date (or set one for the first
      //      time) AND there's existing history → preserve the
      //      older entries, replace the most-recent entry's date.
      //   3. User set a date and the history was empty → seed
      //      a single fresh entry, jurisdiction-sealed under the
      //      new jurisdiction (matches AddRigModal's onboarding
      //      seed shape).
      // The "rigger" on the replaced/new entry uses the existing
      // rigger when present, otherwise "Logbook owner" — the
      // sealed-on event lacks a rigger field in this UI; R.5
      // will own the full event capture.
      const history = rig.repack_history || [];
      if (lastRepackDate !== origDate) {
        if (history.length > 0) {
          const updated = history.map((entry, idx) => {
            if (idx !== history.length - 1) return entry;
            return { ...entry, date: lastRepackDate };
          });
          payload.repack_history = updated;
        } else if (lastRepackDate) {
          payload.repack_history = [{
            date: lastRepackDate,
            rigger: 'Logbook owner',
            jurisdiction_seal: jurisdiction,
          }];
        }
      }

      const updated = await updateRig(rig.id, payload);
      if (onSaved) onSaved(updated);
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

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
          className="rounded-2xl w-full max-w-md overflow-hidden flex flex-col pointer-events-auto mt-10"
          style={{
            background: 'var(--surface-1)',
            border: '0.5px solid var(--border-strong)',
            maxHeight: '85vh',
          }}
        >
          <div
            className="flex items-start justify-between px-5 pt-5 pb-3.5"
            style={{ borderBottom: '0.5px solid var(--border-strong)' }}
          >
            <div>
              <div className="text-[9px] tracking-[0.25em] text-neutral-500 font-medium mb-1">
                EDIT RIG
              </div>
              <div className="text-[19px] font-medium tracking-tight">
                {rig ? `Edit ${rig.nickname}` : 'Edit rig'}
              </div>
              <div className="text-[11px] text-neutral-500 mt-0.5">
                Rename or change jurisdiction. Component swaps live on the My Rig view.
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

          <div className="overflow-y-auto flex-1 p-5 space-y-4">
            {loading && (
              <div className="flex items-center gap-2 text-[12px] text-neutral-500">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                Loading rig…
              </div>
            )}

            {!loading && rig && (
              <>
                <Field label="NICKNAME" required>
                  <input
                    type="text"
                    value={nickname}
                    onChange={(e) => setNickname(e.target.value)}
                    className={inputCls}
                    maxLength={120}
                    placeholder="e.g. Black Vector"
                    disabled={submitting}
                  />
                  <div className="text-[10px] text-neutral-500 mt-1 leading-relaxed">
                    Renaming also renames the folder on disk
                    (<span className="font-mono">rigs/{nickname || rig.nickname}/</span>).
                  </div>
                </Field>

                <Field label="SEALED UNDER" required>
                  <div className="flex gap-1.5">
                    {JURISDICTION_BUTTONS.map((j) => {
                      const active = jurisdiction === j.value;
                      return (
                        <button
                          key={j.value}
                          type="button"
                          onClick={() => setJurisdiction(j.value)}
                          disabled={submitting}
                          className="flex-1 rounded-md px-3 py-2 text-[12px] font-medium transition"
                          style={{
                            background: active ? 'var(--text)' : 'var(--surface-2)',
                            color: active ? 'var(--bg)' : 'var(--text)',
                            border: '0.5px solid var(--border)',
                          }}
                        >
                          {j.label}
                        </button>
                      );
                    })}
                  </div>
                  <div className="text-[10px] text-neutral-500 mt-1 leading-relaxed">
                    USPA 180 d / CSPA 270 d / Both ⇒ the tighter window.
                  </div>
                </Field>

                <Field label="LAST REPACK DATE">
                  <input
                    type="date"
                    value={lastRepackDate}
                    onChange={(e) => setLastRepackDate(e.target.value)}
                    disabled={submitting}
                    className={inputCls}
                  />
                  <div className="text-[10px] text-neutral-500 mt-1 leading-relaxed">
                    {origDate
                      ? 'Updating the date replaces the latest repack entry — older history is kept.'
                      : 'Sets the rig’s first repack seal so the repack-due clock starts ticking.'}
                  </div>
                </Field>
              </>
            )}
          </div>

          <div
            className="flex items-center gap-2 px-5 py-3"
            style={{
              background: 'var(--surface-1)',
              borderTop: '0.5px solid var(--border-strong)',
            }}
          >
            <span className="text-[11px] text-neutral-500">
              {canSave ? 'Saves to rigs/<nickname>/rig.xml.' : 'Make a change to save.'}
            </span>
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
              Save changes
            </button>
          </div>
        </div>
      </div>
    </>
  );
}


function Field({ label, required, children }) {
  return (
    <div>
      <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium mb-1.5">
        {label} {required && <span className="text-neutral-300">*</span>}
      </div>
      {children}
    </div>
  );
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


const inputCls =
  'w-full rounded-md px-3 py-1.5 text-[13px] text-neutral-100 ' +
  'bg-[var(--bg)] border border-[var(--border-strong)] ' +
  'focus:border-[#3a3d41] transition outline-none disabled:opacity-50';
