import React, { useState, useEffect } from 'react';
import { X, Check, Plus, Loader2, AlertTriangle } from 'lucide-react';
import {
  listMains,
  listReserves,
  listAads,
  listContainers,
  createRig,
  ApiError,
} from '../api';

// Slot config — order matters: container first (per D33's sequence
// in <rig>), then main / reserve / aad. The label drives the UI
// copy; ``payloadKey`` is the field name on RigCreate.
const SLOTS = [
  { type: 'container', label: 'CONTAINER', payloadKey: 'current_container_id' },
  { type: 'main',      label: 'MAIN',      payloadKey: 'current_main_id'      },
  { type: 'reserve',   label: 'RESERVE',   payloadKey: 'current_reserve_id'   },
  { type: 'aad',       label: 'AAD',       payloadKey: 'current_aad_id'       },
];

// Jurisdiction button labels → wire values. The backend's
// closed enum is USPA / CSPA / both per D33's Jurisdiction
// simpleType.
const JURISDICTION_BUTTONS = [
  { label: 'USPA', value: 'USPA' },
  { label: 'CSPA', value: 'CSPA' },
  { label: 'Both', value: 'both' },
];


export default function AddRigModal({ visible, onClose, onCreated }) {
  const [name, setName] = useState('');
  const [jurisdiction, setJurisdiction] = useState('USPA');
  // Optional last repack date. When set, the create payload seeds
  // ``repack_history`` with a single entry so the rig's repack
  // countdown clock starts ticking from this date per D38 (the
  // latest entry's date drives the next-repack-due window per the
  // jurisdiction). Rigger name on that entry defaults to
  // "Onboarding entry" — a discoverable marker that distinguishes
  // it from a real rigger-recorded repack landing through R.5.
  const [lastRepackDate, setLastRepackDate] = useState('');
  // ``picked`` holds the chosen Component records keyed by slot
  // type (so we can render their summary inline). ``activeSlot``
  // controls which slot's pool is currently expanded.
  const [picked, setPicked] = useState({
    container: null, main: null, reserve: null, aad: null,
  });
  const [activeSlot, setActiveSlot] = useState('container');

  // Inventory pools — fetched once per modal open. Each list is
  // filtered to the available components (assigned_rig_id === null
  // AND status === 'active') so the user only sees pickable rows.
  const [pools, setPools] = useState({
    container: [], main: [], reserve: [], aad: [],
  });
  const [loadFailed, setLoadFailed] = useState(false);
  const [loading, setLoading] = useState(false);

  // Submit state — separate from load state.
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!visible) {
      document.body.style.overflow = '';
      return;
    }
    document.body.style.overflow = 'hidden';

    // Reset every open so a previous half-finished session (or a
    // failed submit that left state lying around) doesn't bleed
    // into the next attempt.
    setError(null);
    setSubmitting(false);
    setLoadFailed(false);
    setName('');
    setJurisdiction('USPA');
    setLastRepackDate('');
    setPicked({ container: null, main: null, reserve: null, aad: null });
    setActiveSlot('container');

    let cancelled = false;
    setLoading(true);
    Promise.all([
      listContainers({ limit: 1000 }),
      listMains({ limit: 1000 }),
      listReserves({ limit: 1000 }),
      listAads({ limit: 1000 }),
    ])
      .then(([containers, mains, reserves, aads]) => {
        if (cancelled) return;
        const onlyAvailable = (rows) =>
          rows.filter((c) => !c.assigned_rig_id && c.status === 'active');
        setPools({
          container: onlyAvailable(containers),
          main: onlyAvailable(mains),
          reserve: onlyAvailable(reserves),
          aad: onlyAvailable(aads),
        });
      })
      .catch(() => {
        if (!cancelled) setLoadFailed(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      document.body.style.overflow = '';
    };
  }, [visible]);

  if (!visible) return null;

  const filledCount = Object.values(picked).filter((v) => v !== null).length;
  const nameOk = name.trim().length > 0;
  const canSave = filledCount === 4 && nameOk && !submitting;
  // Why is Save disabled, in plain language. Surfaced inline next
  // to the button so the user doesn't have to guess (or scroll up
  // to see the error banner). Empty when canSave is true.
  const disabledReason = submitting
    ? null
    : !nameOk
      ? 'Enter a rig name'
      : filledCount < 4
        ? `Pick the remaining ${4 - filledCount} component${filledCount === 3 ? '' : 's'}`
        : null;

  function handleSelect(slotType, component) {
    setPicked((prev) => ({ ...prev, [slotType]: component }));
    // Auto-advance to the next empty slot, mirroring the prior
    // mock behavior so the user can keep picking without clicking
    // the next slot header.
    const next = SLOTS.find((s) => s.type !== slotType && !picked[s.type]);
    setActiveSlot(next ? next.type : null);
  }

  function handleClear(slotType) {
    setPicked((prev) => ({ ...prev, [slotType]: null }));
    setActiveSlot(slotType);
  }

  async function handleSave() {
    setSubmitting(true);
    setError(null);
    try {
      const payload = {
        nickname: name.trim(),
        jurisdiction,
      };
      for (const slot of SLOTS) {
        payload[slot.payloadKey] = picked[slot.type].id;
      }
      // D38 onboarding path: when the user supplied a "last repack
      // date", seed repack_history with one entry. The jurisdiction
      // seal mirrors the rig's jurisdiction (USPA-only rig → USPA
      // seal; "both" rig → both seal). The rigger field is required
      // by the XSD min-length rule; we mark this entry as
      // ``Onboarding entry`` so it's discoverable as bootstrap data
      // distinct from real rigger-recorded repacks (R.5 territory).
      if (lastRepackDate) {
        payload.repack_history = [
          {
            date: lastRepackDate,
            rigger: 'Onboarding entry',
            jurisdiction_seal: jurisdiction,
          },
        ];
      }
      const created = await createRig(payload);
      if (onCreated) onCreated(created);
      // Reset form so the next open is fresh.
      setName('');
      setJurisdiction('USPA');
      setLastRepackDate('');
      setPicked({ container: null, main: null, reserve: null, aad: null });
      setActiveSlot('container');
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
          className="rounded-2xl w-full max-w-xl overflow-hidden flex flex-col pointer-events-auto mt-10"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)', maxHeight: '85vh' }}
        >
          <div className="flex items-start justify-between px-5 pt-5 pb-3.5" style={{ borderBottom: '0.5px solid var(--border-strong)' }}>
            <div>
              <div className="text-[9px] tracking-[0.25em] text-neutral-500 font-medium mb-1">NEW RIG</div>
              <div className="text-[19px] font-medium tracking-tight">Build a rig</div>
              <div className="text-[11px] text-neutral-500 mt-0.5">Compose four components from inventory.</div>
            </div>
            <button
              onClick={onClose}
              disabled={submitting}
              className="w-8 h-8 rounded-lg flex items-center justify-center transition hover:bg-neutral-800 disabled:opacity-50"
              style={{ background: 'var(--surface-2)' }}
            >
              <X className="w-3.5 h-3.5 text-neutral-400" />
            </button>
          </div>

          {error && <ErrorBanner error={error} />}
          {loadFailed && !error && (
            <div className="px-5 py-2.5 text-[12px] text-amber-300" style={{ background: 'rgba(251,191,36,0.06)', borderBottom: '0.5px solid var(--border-strong)' }}>
              <AlertTriangle className="w-3.5 h-3.5 inline-block mr-1.5" />
              Couldn't load inventory. Add components on the Inventory page first.
            </div>
          )}

          <div className="px-5 py-4 space-y-3" style={{ borderBottom: '0.5px solid var(--border-strong)' }}>
            <div>
              <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium mb-1.5">RIG NAME</div>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Sport rig"
                className="w-full rounded-md px-3 py-1.5 text-[13px] text-neutral-100"
                style={{ background: 'var(--bg)', border: '0.5px solid var(--border-strong)' }}
              />
            </div>
            <div className="grid grid-cols-[1fr_1fr] gap-3">
              <div>
                <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium mb-1.5">JURISDICTION</div>
                <div
                  className="inline-flex gap-0.5 p-0.5 rounded-md"
                  style={{ background: 'var(--bg)', border: '0.5px solid var(--border-strong)' }}
                >
                  {JURISDICTION_BUTTONS.map((j) => (
                    <button
                      key={j.value}
                      onClick={() => setJurisdiction(j.value)}
                      className="px-2.5 py-1 rounded text-[11px] transition"
                      style={{
                        background: jurisdiction === j.value ? 'var(--surface-3)' : 'transparent',
                        color: jurisdiction === j.value ? 'var(--text)' : 'var(--text-faint)',
                      }}
                    >
                      {j.label}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium mb-1.5">
                  LAST REPACK DATE
                  <span className="text-neutral-600 normal-case tracking-normal ml-1.5">optional</span>
                </div>
                <input
                  type="date"
                  value={lastRepackDate}
                  onChange={(e) => setLastRepackDate(e.target.value)}
                  className="w-full rounded-md px-3 py-1.5 text-[13px] text-neutral-100"
                  style={{ background: 'var(--bg)', border: '0.5px solid var(--border-strong)' }}
                />
                <div className="text-[10px] text-neutral-600 mt-1">
                  Sets the {jurisdiction === 'both'
                    ? 'USPA + CSPA'
                    : jurisdiction} repack clock.
                </div>
              </div>
            </div>
          </div>

          <div className="overflow-y-auto flex-1 p-4">
            <div className="text-[10px] tracking-[0.25em] text-neutral-500 font-medium mb-2.5">COMPONENTS</div>
            {loading ? (
              <div className="flex items-center gap-2 text-[12px] text-neutral-500 py-4">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                Loading inventory…
              </div>
            ) : (
              <div className="space-y-1.5">
                {SLOTS.map((slot) => (
                  <Slot
                    key={slot.type}
                    slot={slot}
                    selected={picked[slot.type]}
                    active={activeSlot === slot.type}
                    pool={activeSlot === slot.type ? pools[slot.type] : []}
                    onSetActive={() => setActiveSlot(slot.type)}
                    onSelect={(item) => handleSelect(slot.type, item)}
                    onClear={() => handleClear(slot.type)}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Inline error near the Save button so the user sees
              the failure reason without scrolling up to the top
              banner. Only renders when a submit attempt failed. */}
          {error && <FooterErrorBanner error={error} />}
          <div className="flex items-center gap-2 px-5 py-3" style={{ background: 'var(--surface-1)', borderTop: '0.5px solid var(--border-strong)' }}>
            <span className="text-[11px] text-neutral-500">
              <span className="font-mono text-neutral-400">{filledCount} of 4</span> components selected
              {disabledReason && (
                <span className="text-[11px] text-amber-300 ml-2">
                  · {disabledReason}
                </span>
              )}
            </span>
            <div className="flex-1" />
            <button
              onClick={onClose}
              disabled={submitting}
              className="px-3 py-1.5 text-[12px] text-neutral-400 transition hover:text-neutral-200 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              disabled={!canSave}
              onClick={handleSave}
              className="px-3.5 py-1.5 rounded-md text-[12px] font-medium transition flex items-center gap-1.5"
              style={{
                background: canSave ? 'var(--text)' : 'var(--surface-3)',
                color: canSave ? 'var(--bg)' : 'var(--text-faint)',
                cursor: canSave ? 'pointer' : 'not-allowed',
              }}
            >
              {submitting && <Loader2 className="w-3 h-3 animate-spin" />}
              Save rig
            </button>
          </div>
        </div>
      </div>
    </>
  );
}


function ErrorBanner({ error }) {
  // Render the RFC 9457 problem+json detail when present, fall
  // back to the raw message otherwise. ApiError carries
  // ``problem`` for the parsed body.
  let message = String(error.message || error);
  let pointers = [];
  if (error instanceof ApiError && error.problem) {
    message = error.problem.detail || message;
    if (Array.isArray(error.problem.errors)) {
      pointers = error.problem.errors;
    }
  }
  return (
    <div className="px-5 py-2.5 text-[12px]" style={{ background: 'rgba(248,113,113,0.06)', color: 'var(--status-critical)', borderBottom: '0.5px solid var(--border-strong)' }}>
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


// Compact variant of ErrorBanner rendered just above the footer
// row so the user sees the failure reason adjacent to the Save
// button (rather than only at the top of the modal, which scrolls
// off-screen on tall forms).
function FooterErrorBanner({ error }) {
  let message = String(error.message || error);
  let pointers = [];
  if (error instanceof ApiError && error.problem) {
    message = error.problem.detail || message;
    if (Array.isArray(error.problem.errors)) {
      pointers = error.problem.errors;
    }
  }
  return (
    <div className="px-5 py-2 text-[12px]" style={{ background: 'rgba(248,113,113,0.08)', color: 'var(--status-critical)', borderTop: '0.5px solid var(--border-strong)' }}>
      <div className="flex items-start gap-2">
        <AlertTriangle className="w-3.5 h-3.5 mt-0.5" />
        <div className="flex-1 min-w-0">
          <div className="font-medium">Save failed</div>
          <div className="text-[11px] mt-0.5">{message}</div>
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


function Slot({ slot, selected, active, pool, onSetActive, onSelect, onClear }) {
  if (selected) {
    // Selected component summary. Pull manufacturer / model /
    // size_sqft from the real entity shape (size_sqft on
    // main/reserve, size on container, no size on AAD).
    const sizeBit = selected.size_sqft != null
      ? ` ${Number(selected.size_sqft)}`
      : selected.size
        ? ` ${selected.size}`
        : '';
    const brand = selected.manufacturer || '—';
    const model = selected.model || 'unknown';
    return (
      <div
        className="flex items-center gap-3 px-3 py-2.5 rounded-lg"
        style={{ background: 'var(--bg)', border: '0.5px solid var(--border-strong)' }}
      >
        <Check className="w-4 h-4" style={{ color: 'var(--status-ready)' }} strokeWidth={2.2} />
        <div className="flex-1 min-w-0">
          <div className="text-[9px] tracking-[0.25em] text-neutral-500 font-medium">{slot.label}</div>
          <div className="text-[13px] text-neutral-100 mt-0.5 truncate">
            {brand} {model}{sizeBit}
            {selected.serial && (
              <span className="text-[11px] text-neutral-500 font-mono ml-1.5">SN {selected.serial}</span>
            )}
          </div>
        </div>
        <button onClick={onClear} className="text-[11px] text-neutral-500 hover:text-neutral-300">
          Change
        </button>
      </div>
    );
  }

  if (active) {
    return (
      <div
        className="rounded-lg overflow-hidden"
        style={{
          background: 'var(--bg)',
          border: '0.5px solid var(--status-ready)',
          boxShadow: '0 0 0 1px rgba(52,211,153,0.15)',
        }}
      >
        <div className="flex items-center gap-3 px-3 py-2.5">
          <div
            className="w-4 h-4 rounded-full flex items-center justify-center"
            style={{ border: '1.5px solid var(--status-ready)' }}
          >
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--status-ready)' }} />
          </div>
          <div className="flex-1">
            <div className="text-[9px] tracking-[0.25em] font-medium" style={{ color: 'var(--status-ready)' }}>
              {slot.label} · CHOOSING
            </div>
            <div className="text-[12px] text-neutral-400 mt-0.5">
              Pick from your unassigned {slot.label.toLowerCase()}s
            </div>
          </div>
        </div>
        <div style={{ borderTop: '0.5px solid #1f2226', background: 'var(--surface-1)' }}>
          {pool.length === 0 ? (
            <div className="p-3 text-[12px] text-neutral-500 text-center italic">
              Nothing available — add one on the Inventory page.
            </div>
          ) : (
            pool.map((opt) => {
              const sizeBit = opt.size_sqft != null
                ? ` ${Number(opt.size_sqft)}`
                : opt.size
                  ? ` ${opt.size}`
                  : '';
              const brand = opt.manufacturer || '—';
              const model = opt.model || 'unknown';
              const dom = opt.date_of_manufacture
                ? opt.date_of_manufacture.replace(/-/g, '/')
                : '—';
              return (
                <button
                  key={opt.id}
                  onClick={() => onSelect(opt)}
                  className="w-full grid items-center px-3 py-2.5 text-left transition hover:bg-neutral-800/40"
                  style={{ gridTemplateColumns: '18px 1fr 80px', gap: 10, borderBottom: '0.5px solid var(--surface-2)' }}
                >
                  <span
                    className="inline-block ml-1"
                    style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--status-ready)', boxShadow: '0 0 8px rgba(52,211,153,0.4)' }}
                  />
                  <div>
                    <div className="text-[13px] text-neutral-100">{brand} {model}{sizeBit}</div>
                    <div className="text-[11px] text-neutral-500 font-mono">
                      {opt.serial ? `SN ${opt.serial} · ` : ''}DOM {dom}
                      {opt.repack_count_initial !== undefined && opt.repack_limit
                        ? ` · ${opt.repack_count_initial}/${opt.repack_limit} repacks`
                        : ''}
                    </div>
                  </div>
                  <span className="text-right text-[11px] tracking-[0.15em] text-neutral-400">AVAILABLE</span>
                </button>
              );
            })
          )}
        </div>
      </div>
    );
  }

  return (
    <button
      onClick={onSetActive}
      className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition hover:bg-neutral-900"
      style={{ background: 'var(--bg)', border: '0.5px dashed var(--text-faint)' }}
    >
      <div className="w-4 h-4 rounded-full" style={{ border: '1.5px dashed var(--text-faint)' }} />
      <div className="flex-1">
        <div className="text-[9px] tracking-[0.25em] text-neutral-500 font-medium">{slot.label}</div>
        <div className="text-[12px] text-neutral-600 mt-0.5 italic">Pick from inventory…</div>
      </div>
    </button>
  );
}
