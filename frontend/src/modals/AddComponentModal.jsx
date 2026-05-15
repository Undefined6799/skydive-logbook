import React, { useEffect, useState } from 'react';
import { X, Loader2, AlertTriangle } from 'lucide-react';
import {
  createMain,
  createReserve,
  createAad,
  createContainer,
  ApiError,
} from '../api';
import { LINE_MATERIALS, composeLineType } from '../lineTypes';

// Per D33 + D34: four kinds. Each maps to its own backend endpoint
// and has slightly different fields. The modal renders the common
// identification fields first (manufacturer / model / serial / DOM)
// then per-kind fields below.
const KINDS = [
  { value: 'main',      label: 'Main',      sub: 'Main canopy' },
  { value: 'reserve',   label: 'Reserve',   sub: 'Reserve canopy' },
  { value: 'aad',       label: 'AAD',       sub: 'Automatic activation device' },
  { value: 'container', label: 'Container', sub: 'Container / harness' },
];

const CREATORS = {
  main: createMain,
  reserve: createReserve,
  aad: createAad,
  container: createContainer,
};

// ``initialKind`` lets callers pre-select the kind on open. The
// AddRigModal pops this with the matching slot kind when the user
// hits the "Add a container/main/…" affordance in an empty pool,
// so the user doesn't have to re-pick the kind they were already
// missing. Defaults to ``'main'`` to preserve the existing
// open-from-Inventory behaviour.
export default function AddComponentModal({ visible, onClose, onCreated, initialKind = 'main' }) {
  const [kind, setKind] = useState(initialKind);
  const [form, setForm] = useState(emptyForm());
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
    setKind(initialKind);
    setForm(emptyForm());
    return () => {
      document.body.style.overflow = '';
    };
  }, [visible, initialKind]);

  if (!visible) return null;

  function update(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function handleSave(e) {
    e?.preventDefault?.();
    setSubmitting(true);
    setError(null);
    try {
      const payload = buildPayload(kind, form);
      const created = await CREATORS[kind](payload);
      if (onCreated) onCreated(created);
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
        <form
          onSubmit={handleSave}
          onClick={(e) => e.stopPropagation()}
          className="rounded-2xl w-full max-w-xl overflow-hidden flex flex-col pointer-events-auto mt-10"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)', maxHeight: '85vh' }}
        >
          <div className="flex items-start justify-between px-5 pt-5 pb-3.5" style={{ borderBottom: '0.5px solid var(--border-strong)' }}>
            <div>
              <div className="text-[9px] tracking-[0.25em] text-neutral-500 font-medium mb-1">NEW COMPONENT</div>
              <div className="text-[19px] font-medium tracking-tight">Add component</div>
              <div className="text-[11px] text-neutral-500 mt-0.5">
                Drop a piece of gear into inventory. Assign to a rig later.
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="w-8 h-8 rounded-lg flex items-center justify-center transition hover:bg-neutral-800 disabled:opacity-50"
              style={{ background: 'var(--surface-2)' }}
            >
              <X className="w-3.5 h-3.5 text-neutral-400" />
            </button>
          </div>

          {error && <ErrorBanner error={error} />}

          <div className="px-5 py-4" style={{ borderBottom: '0.5px solid var(--border-strong)' }}>
            <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium mb-2">KIND</div>
            <div className="grid grid-cols-4 gap-1">
              {KINDS.map((k) => (
                <button
                  type="button"
                  key={k.value}
                  onClick={() => setKind(k.value)}
                  className="rounded-md px-2 py-2 text-left transition"
                  style={{
                    background: kind === k.value ? 'var(--surface-3)' : 'var(--bg)',
                    border: `0.5px solid ${kind === k.value ? 'var(--status-ready)' : 'var(--border-strong)'}`,
                    boxShadow: kind === k.value ? '0 0 0 1px rgba(52,211,153,0.15)' : 'none',
                  }}
                >
                  <div className="text-[12px] font-medium" style={{ color: kind === k.value ? 'var(--text)' : 'var(--text-muted)' }}>
                    {k.label}
                  </div>
                  <div className="text-[10px] text-neutral-600 mt-0.5">{k.sub}</div>
                </button>
              ))}
            </div>
          </div>

          <div className="overflow-y-auto flex-1 p-5 space-y-3">
            <Section label="IDENTIFICATION">
              <FormGrid>
                <Field label="MANUFACTURER">
                  <input
                    value={form.manufacturer}
                    onChange={(e) => update('manufacturer', e.target.value)}
                    placeholder="e.g. Performance Designs"
                    className={inputCls}
                  />
                </Field>
                <Field label="MODEL">
                  <input
                    value={form.model}
                    onChange={(e) => update('model', e.target.value)}
                    placeholder={
                      kind === 'main' ? 'e.g. Sabre 3'
                      : kind === 'reserve' ? 'e.g. Optimum'
                      : kind === 'aad' ? 'e.g. Cypres 2'
                      : 'e.g. Vector V348'
                    }
                    className={inputCls}
                  />
                </Field>
              </FormGrid>
              <FormGrid>
                <Field label="SERIAL">
                  <input
                    value={form.serial}
                    onChange={(e) => update('serial', e.target.value)}
                    placeholder="manufacturer SN"
                    className={inputCls}
                  />
                </Field>
                <Field label="DATE OF MANUFACTURE">
                  <input
                    type="date"
                    value={form.date_of_manufacture}
                    onChange={(e) => update('date_of_manufacture', e.target.value)}
                    className={inputCls}
                  />
                </Field>
              </FormGrid>
            </Section>

            {(kind === 'main' || kind === 'reserve') && (
              <Section label="GEOMETRY & COUNTERS">
                <FormGrid>
                  <Field label="SIZE (sqft)">
                    <input
                      type="number"
                      step="0.1"
                      min="0"
                      value={form.size_sqft}
                      onChange={(e) => update('size_sqft', e.target.value)}
                      placeholder={kind === 'main' ? 'e.g. 170' : 'e.g. 143'}
                      className={inputCls}
                    />
                  </Field>
                  {kind === 'main' && (
                    <Field label="JUMP COUNT (initial)">
                      <input
                        type="number"
                        min="0"
                        value={form.jump_count_initial}
                        onChange={(e) => update('jump_count_initial', e.target.value)}
                        placeholder="0 for new gear"
                        className={inputCls}
                      />
                    </Field>
                  )}
                </FormGrid>
                {kind === 'main' && (
                  /* D45: RDS flag. Per Peelman's wear-math, an RDS
                     canopy consumes +0.15 lb of line budget on every
                     jump. The +0.15 calculation is R.4 — for now this
                     just stores the flag so used-gear onboarding
                     captures it at create time. */
                  <label className="flex items-start gap-2 mt-1 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={!!form.has_rds}
                      onChange={(e) => update('has_rds', e.target.checked)}
                      className="mt-0.5 w-3.5 h-3.5 rounded"
                      style={{ accentColor: 'var(--status-ready)' }}
                    />
                    <span className="flex-1">
                      <span className="block text-[12px] text-neutral-200">
                        Removable deployment system (RDS)
                      </span>
                      <span className="block text-[10px] text-neutral-500 mt-0.5">
                        Adds +0.15 lb of line wear per jump (D45). Check
                        if this canopy has a removable slider / collapsible
                        pilot chute setup.
                      </span>
                    </span>
                  </label>
                )}
                {kind === 'reserve' && (
                  <FormGrid>
                    <Field label="REPACK COUNT (initial)">
                      <input
                        type="number"
                        min="0"
                        value={form.repack_count_initial}
                        onChange={(e) => update('repack_count_initial', e.target.value)}
                        placeholder="0 for new gear"
                        className={inputCls}
                      />
                    </Field>
                    <Field label="RIDE COUNT (initial)">
                      <input
                        type="number"
                        min="0"
                        value={form.ride_count_initial}
                        onChange={(e) => update('ride_count_initial', e.target.value)}
                        placeholder="0 for new gear"
                        className={inputCls}
                      />
                    </Field>
                  </FormGrid>
                )}
                {kind === 'reserve' && (
                  <FormGrid>
                    <Field label="REPACK LIMIT">
                      <input
                        type="number"
                        min="0"
                        value={form.repack_limit}
                        onChange={(e) => update('repack_limit', e.target.value)}
                        placeholder="manufacturer spec"
                        className={inputCls}
                      />
                    </Field>
                    <Field label="RIDE LIMIT">
                      <input
                        type="number"
                        min="0"
                        value={form.ride_limit}
                        onChange={(e) => update('ride_limit', e.target.value)}
                        placeholder="manufacturer spec"
                        className={inputCls}
                      />
                    </Field>
                  </FormGrid>
                )}
              </Section>
            )}

            {kind === 'main' && (
              <Section label="CURRENT LINESET (optional)">
                <div className="text-[11px] text-neutral-500 mb-2">
                  Fill these in if the canopy is currently lined.
                  D45's wear math reads from here. Leave MATERIAL
                  blank to record the main as "not yet lined".
                </div>
                <FormGrid>
                  <Field label="MATERIAL">
                    <select
                      value={form.line_material}
                      onChange={(e) => {
                        const m = e.target.value;
                        update('line_material', m);
                        // Switching material invalidates the variant.
                        update('line_variant', '');
                      }}
                      className={inputCls}
                    >
                      <option value="">— not yet lined —</option>
                      {Object.entries(LINE_MATERIALS).map(([k, v]) => (
                        <option key={k} value={k}>{v.label}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label={form.line_material === 'other' ? 'TYPE (free text)' : 'VARIANT'}>
                    {form.line_material === 'other' ? (
                      <input
                        value={form.line_variant}
                        onChange={(e) => update('line_variant', e.target.value)}
                        placeholder="custom line description"
                        className={inputCls}
                      />
                    ) : (
                      <select
                        value={form.line_variant}
                        onChange={(e) => {
                          const v = e.target.value;
                          update('line_variant', v);
                          // Auto-fill breaking strength from the
                          // chosen variant unless the user has
                          // already typed a value. D45's budget
                          // math is the consumer of this number.
                          const variants = LINE_MATERIALS[form.line_material]?.variants || [];
                          const found = variants.find((x) => x.value === v);
                          if (found) update('breaking_strength_lb', String(found.strength));
                        }}
                        disabled={!form.line_material}
                        className={inputCls}
                      >
                        <option value="">— pick a variant —</option>
                        {(LINE_MATERIALS[form.line_material]?.variants || []).map((x) => (
                          <option key={x.value} value={x.value}>
                            {form.line_material === 'vectran' ? x.value : `${LINE_MATERIALS[form.line_material].label} ${x.value}`}
                            {' '}
                            ({x.strength} lb)
                          </option>
                        ))}
                      </select>
                    )}
                  </Field>
                </FormGrid>
                <FormGrid>
                  <Field label="BREAKING STRENGTH (lb)">
                    <input
                      type="number"
                      step="1"
                      min="0"
                      value={form.breaking_strength_lb}
                      onChange={(e) =>
                        update('breaking_strength_lb', e.target.value)
                      }
                      placeholder="auto-fills from variant"
                      className={inputCls}
                    />
                  </Field>
                  <Field label="LINE TYPE (composed)">
                    <input
                      readOnly
                      value={composeLineType(form.line_material, form.line_variant) || '—'}
                      className={inputCls}
                      style={{ opacity: 0.7, cursor: 'default' }}
                    />
                  </Field>
                </FormGrid>
                <FormGrid>
                  <Field label="INSTALL DATE">
                    <input
                      type="date"
                      value={form.install_date}
                      onChange={(e) => update('install_date', e.target.value)}
                      className={inputCls}
                    />
                  </Field>
                  <Field label="INSTALLED BY (optional)">
                    <input
                      value={form.installed_by}
                      onChange={(e) => update('installed_by', e.target.value)}
                      placeholder="rigger name"
                      className={inputCls}
                    />
                  </Field>
                </FormGrid>
                <FormGrid>
                  <Field label="JUMPS ON LINESET (used gear)">
                    <input
                      type="number"
                      step="1"
                      min="0"
                      value={form.jumps_on_lineset_initial}
                      onChange={(e) =>
                        update('jumps_on_lineset_initial', e.target.value)
                      }
                      placeholder="0 for fresh install"
                      className={inputCls}
                    />
                  </Field>
                  <div />
                </FormGrid>
                <div className="text-[11px] text-neutral-500 leading-relaxed">
                  Exit weight comes from your jumper profile (D46),
                  not snapshotted here. The line-wear math reads it
                  live each time it computes residual budget — moving
                  the canopy to a different jumper picks up the new
                  weight automatically.
                </div>
              </Section>
            )}

            {kind === 'aad' && (
              <Section label="AAD MODE & COUNTERS">
                <FormGrid>
                  <Field label="MODE">
                    <input
                      value={form.mode}
                      onChange={(e) => update('mode', e.target.value)}
                      placeholder="e.g. Pro / Expert / Tandem"
                      className={inputCls}
                    />
                  </Field>
                  <Field label="MODE CHANGEABLE">
                    <select
                      value={form.is_changeable_mode}
                      onChange={(e) => update('is_changeable_mode', e.target.value)}
                      className={inputCls}
                    >
                      <option value="">unknown</option>
                      <option value="true">yes</option>
                      <option value="false">no</option>
                    </select>
                  </Field>
                </FormGrid>
                <FormGrid>
                  <Field label="JUMP COUNT (initial)">
                    <input
                      type="number"
                      min="0"
                      value={form.jump_count_initial}
                      onChange={(e) => update('jump_count_initial', e.target.value)}
                      placeholder="0 for new gear"
                      className={inputCls}
                    />
                  </Field>
                  <Field label="FIRE COUNT (initial)">
                    <input
                      type="number"
                      min="0"
                      value={form.fire_count_initial}
                      onChange={(e) => update('fire_count_initial', e.target.value)}
                      placeholder="0 for new gear"
                      className={inputCls}
                    />
                  </Field>
                </FormGrid>
              </Section>
            )}

            {kind === 'container' && (
              <Section label="GEOMETRY & COUNTERS">
                <FormGrid>
                  <Field label="SIZE (free text)">
                    <input
                      value={form.size}
                      onChange={(e) => update('size', e.target.value)}
                      placeholder='e.g. "M22", "Large"'
                      className={inputCls}
                    />
                  </Field>
                  <Field label="JUMP COUNT (initial)">
                    <input
                      type="number"
                      min="0"
                      value={form.jump_count_initial}
                      onChange={(e) => update('jump_count_initial', e.target.value)}
                      placeholder="0 for new gear"
                      className={inputCls}
                    />
                  </Field>
                </FormGrid>
              </Section>
            )}
          </div>

          <div className="flex items-center gap-2 px-5 py-3" style={{ background: 'var(--surface-1)', borderTop: '0.5px solid var(--border-strong)' }}>
            <span className="text-[11px] text-neutral-500">
              Saves to inventory unassigned. Pick it into a rig later from
              "Add rig".
            </span>
            <div className="flex-1" />
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="px-3 py-1.5 text-[12px] text-neutral-400 transition hover:text-neutral-200 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-3.5 py-1.5 rounded-md text-[12px] font-medium transition flex items-center gap-1.5"
              style={{
                background: submitting ? 'var(--surface-3)' : 'var(--text)',
                color: submitting ? 'var(--text-faint)' : 'var(--bg)',
                cursor: submitting ? 'not-allowed' : 'pointer',
              }}
            >
              {submitting && <Loader2 className="w-3 h-3 animate-spin" />}
              Save component
            </button>
          </div>
        </form>
      </div>
    </>
  );
}


function emptyForm() {
  return {
    manufacturer: '',
    model: '',
    serial: '',
    date_of_manufacture: '',
    // main / reserve
    size_sqft: '',
    // container
    size: '',
    // counters (per-kind subset is read at submit)
    jump_count_initial: '0',
    fire_count_initial: '0',
    repack_count_initial: '0',
    ride_count_initial: '0',
    repack_limit: '',
    ride_limit: '',
    // main D45 RDS flag. Boolean here; converted on submit. Default
    // false matches the backend model and keeps unmodified canopies
    // out of the +0.15 lb wear delta when R.4 wires it up.
    has_rds: false,
    // aad
    mode: '',
    is_changeable_mode: '',  // '' | 'true' | 'false'
    // main current_lineset (optional — Lineset payload assembled
    // only when material+variant are both filled). The on-disk
    // line_type string is composed at submit time as
    // "<MaterialLabel> <variant>" (Vectran V750, HMA 825, …).
    // Per D46: install_exit_weight_lb is dropped (live-read from
    // the jumper profile); consumed_lb_initial is replaced with
    // jumps_on_lineset_initial (int).
    line_material: '',
    line_variant: '',
    breaking_strength_lb: '',
    install_date: new Date().toISOString().slice(0, 10),
    installed_by: '',
    jumps_on_lineset_initial: '0',
  };
}


function buildPayload(kind, form) {
  // Common identification fields. Empty strings → null on the
  // wire so the backend doesn't reject "" as a "min length 1"
  // failure where the field is actually optional.
  const common = {
    status: 'active',
    manufacturer: form.manufacturer.trim() || null,
    model: form.model.trim() || null,
    serial: form.serial.trim() || null,
    date_of_manufacture: form.date_of_manufacture || null,
  };
  const numOrZero = (v) => {
    const n = parseInt(v, 10);
    return Number.isFinite(n) ? n : 0;
  };
  const numOrNull = (v) => {
    if (v === '' || v == null) return null;
    const n = parseInt(v, 10);
    return Number.isFinite(n) ? n : null;
  };
  const floatOrNull = (v) => {
    if (v === '' || v == null) return null;
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : null;
  };

  if (kind === 'main') {
    const payload = {
      ...common,
      size_sqft: floatOrNull(form.size_sqft),
      // D45: RDS flag rides on the create payload. Boolean — the
      // backend's Main model treats it as service-controlled in the
      // sense that the XSD elides false to keep XML compact, but
      // it's user-controllable via this form (and via PUT in the
      // edit path).
      has_rds: !!form.has_rds,
      jump_count_initial: numOrZero(form.jump_count_initial),
    };
    // If the user filled in MATERIAL+VARIANT (or chose Other and
    // typed a free-form value), include a current_lineset block.
    // All scalar fields are required by the Lineset model — ge=0
    // / gt=0 constraints surface as 422s with field pointers.
    // Missing material/variant → main is "not yet lined" and
    // current_lineset is omitted.
    //
    // Per D46: install_exit_weight_lb is no longer on the Lineset
    // (live-read from jumper.exit_weight_lb instead);
    // consumed_lb_initial is replaced with jumps_on_lineset_initial
    // (int — count of pre-logbook jumps on this lineset).
    const composedLineType = composeLineType(form.line_material, form.line_variant);
    if (composedLineType) {
      payload.current_lineset = {
        line_type: composedLineType,
        breaking_strength_lb: floatOrNull(form.breaking_strength_lb),
        install_date: form.install_date,
        installed_by: form.installed_by.trim() || null,
        jumps_on_lineset_initial: numOrZero(form.jumps_on_lineset_initial),
      };
    }
    return payload;
  }
  if (kind === 'reserve') {
    return {
      ...common,
      size_sqft: floatOrNull(form.size_sqft),
      repack_limit: numOrNull(form.repack_limit),
      ride_limit: numOrNull(form.ride_limit),
      repack_count_initial: numOrZero(form.repack_count_initial),
      ride_count_initial: numOrZero(form.ride_count_initial),
    };
  }
  if (kind === 'aad') {
    return {
      ...common,
      mode: form.mode.trim() || null,
      is_changeable_mode:
        form.is_changeable_mode === 'true' ? true
        : form.is_changeable_mode === 'false' ? false
        : null,
      jump_count_initial: numOrZero(form.jump_count_initial),
      fire_count_initial: numOrZero(form.fire_count_initial),
    };
  }
  if (kind === 'container') {
    return {
      ...common,
      size: form.size.trim() || null,
      jump_count_initial: numOrZero(form.jump_count_initial),
    };
  }
  return common;
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


function Section({ label, children }) {
  return (
    <div>
      <div className="text-[10px] tracking-[0.25em] text-neutral-500 font-medium mb-2">
        {label}
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}


function FormGrid({ children }) {
  return <div className="grid grid-cols-2 gap-3">{children}</div>;
}


function Field({ label, children }) {
  return (
    <div>
      <div className="text-[9px] tracking-[0.2em] text-neutral-500 font-medium mb-1">
        {label}
      </div>
      {children}
    </div>
  );
}


const inputCls =
  'w-full rounded-md px-3 py-1.5 text-[13px] text-neutral-100 bg-[var(--bg)] border border-neutral-800 focus:border-neutral-600 focus:outline-none';
