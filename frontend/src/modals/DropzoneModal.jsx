import React, { useState, useEffect } from 'react';
import { X, AlertTriangle, Plus, Loader2, Plane, Trash2 } from 'lucide-react';
import { createDropzone, updateDropzone, ApiError } from '../api';

// D45 environment enum. Order matches the wear-math table — clean
// first, escalating from there.
const ENVIRONMENTS = [
  {
    value: 'clean_grass',
    label: 'Clean grass',
    delta: '+0.00',
    blurb: 'Manicured turf, pavement, or other low-abrasion surface.',
  },
  {
    value: 'dust_sand_salt',
    label: 'Dust / sand / salt',
    delta: '+0.20',
    blurb: 'Coastal DZ, packjobs near a beach, salty or dusty air.',
  },
  {
    value: 'desert',
    label: 'Desert',
    delta: '+0.25',
    blurb: 'Arid, fine-particle environment (Eloy, Skydive Arizona).',
  },
];

// Two modes:
//   'create' — POST a new dropzone.
//   'edit'   — PUT an existing one (initialDropzone preloaded).
export default function DropzoneModal({
  visible,
  onClose,
  onCreated,
  onUpdated,
  mode = 'create',
  initialDropzone = null,
}) {
  const isEdit = mode === 'edit' && initialDropzone != null;
  const [form, setForm] = useState({
    name: '',
    city: '',
    province: '',
    country: '',
    environment: 'clean_grass',
    aircraft: [],   // list of { model, tail_number? }
    notes: '',
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!visible) {
      setSubmitting(false);
      return;
    }
    setError(null);
    if (isEdit) {
      setForm({
        name: initialDropzone.name,
        city: initialDropzone.city,
        province: initialDropzone.province || '',
        country: initialDropzone.country,
        environment: initialDropzone.environment,
        // Hydrate the fleet, defaulting to [] so an older record
        // without the field (pre-R.D.6) still loads cleanly.
        aircraft: (initialDropzone.aircraft || []).map((p) => ({
          model: p.model,
          tail_number: p.tail_number || '',
        })),
        notes: initialDropzone.notes || '',
      });
    } else {
      setForm({
        name: '',
        city: '',
        province: '',
        country: '',
        environment: 'clean_grass',
        aircraft: [],
        notes: '',
      });
    }
  }, [visible, isEdit, initialDropzone]);

  // Lock body scroll while the modal is open.
  useEffect(() => {
    if (visible) document.body.style.overflow = 'hidden';
    else document.body.style.overflow = '';
    return () => { document.body.style.overflow = ''; };
  }, [visible]);

  if (!visible) return null;

  const update = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  function buildPayload() {
    return {
      name: form.name.trim(),
      city: form.city.trim(),
      // Optional fields collapse empty string → null so the backend
      // sees the absence semantically rather than as ''.
      province: form.province.trim() || null,
      // The XSD pattern is strict [A-Z]{2}; the backend will 422 on
      // lowercase. Uppercase here so the user doesn't need to think
      // about case (the API still gates on it as a safety net).
      country: form.country.trim().toUpperCase(),
      environment: form.environment,
      // Drop empty rows; trim each field; collapse blank tail
      // numbers to null so the backend writes ``<tail_number>``
      // only when the user actually filled it in.
      aircraft: form.aircraft
        .map((p) => ({
          model: (p.model || '').trim(),
          tail_number: (p.tail_number || '').trim() || null,
        }))
        .filter((p) => p.model.length > 0),
      notes: form.notes.trim() || null,
    };
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (isEdit) {
        const updated = await updateDropzone(initialDropzone.id, buildPayload());
        onUpdated(updated);
      } else {
        const created = await createDropzone(buildPayload());
        onCreated(created);
      }
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
      <div className="fixed inset-0 z-50 flex items-start justify-center p-6 pointer-events-none overflow-y-auto">
        <form
          onClick={(e) => e.stopPropagation()}
          onSubmit={handleSubmit}
          className="rounded-2xl w-full max-w-xl pointer-events-auto mt-10 mb-10 flex flex-col"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)', maxHeight: 'calc(100vh - 80px)' }}
        >
          <div
            className="flex items-start justify-between px-5 pt-5 pb-3.5"
            style={{ borderBottom: '0.5px solid var(--border-strong)' }}
          >
            <div>
              <div className="text-[9px] tracking-[0.25em] text-neutral-500 font-medium mb-1">
                {isEdit ? 'EDIT DROPZONE' : 'NEW DROPZONE'}
              </div>
              <div className="text-[19px] font-medium tracking-tight">
                {isEdit ? `Edit ${initialDropzone.name}` : 'Add a dropzone'}
              </div>
              <div className="text-[11px] text-neutral-500 mt-0.5">
                {isEdit
                  ? 'Edits propagate to lineset-wear calculations on the next reindex.'
                  : 'Environment feeds lineset-wear projections.'}
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="w-8 h-8 rounded-lg flex items-center justify-center transition hover:bg-neutral-800"
              style={{ background: 'var(--surface-2)' }}
            >
              <X className="w-3.5 h-3.5 text-neutral-400" />
            </button>
          </div>

          {error && <ErrorBanner error={error} />}

          <div className="overflow-y-auto flex-1 p-5 space-y-4">
            <Field label="NAME" required>
              <input
                type="text"
                required
                maxLength={120}
                value={form.name}
                onChange={update('name')}
                className={inputCls}
                placeholder="e.g. Parachutisme Adrénaline"
              />
            </Field>

            <FormGrid>
              <Field label="CITY" required>
                <input
                  type="text"
                  required
                  maxLength={120}
                  value={form.city}
                  onChange={update('city')}
                  className={inputCls}
                  placeholder="e.g. Saint-Jérôme"
                />
              </Field>
              <Field label="PROVINCE / STATE">
                <input
                  type="text"
                  value={form.province}
                  onChange={update('province')}
                  className={inputCls}
                  placeholder="Optional"
                />
              </Field>
            </FormGrid>

            <Field label="COUNTRY" required>
              <input
                type="text"
                required
                value={form.country}
                onChange={update('country')}
                onBlur={(e) => setForm({ ...form, country: e.target.value.toUpperCase() })}
                className={inputCls}
                placeholder="ISO 3166-1 alpha-2 (CA, US, FR…)"
                maxLength={2}
                pattern="[A-Za-z]{2}"
                style={{ textTransform: 'uppercase' }}
              />
            </Field>

            <Field label="JUMPING ENVIRONMENT" required>
              <div className="grid grid-cols-1 gap-1.5">
                {ENVIRONMENTS.map((env) => {
                  const active = form.environment === env.value;
                  return (
                    <label
                      key={env.value}
                      className="flex items-start gap-3 rounded-lg p-3 cursor-pointer transition"
                      style={{
                        background: active ? 'var(--accent-soft)' : 'var(--bg)',
                        border: active
                          ? '0.5px solid var(--accent)'
                          : '0.5px solid var(--border-strong)',
                      }}
                    >
                      <input
                        type="radio"
                        name="environment"
                        value={env.value}
                        checked={active}
                        onChange={update('environment')}
                        className="mt-1"
                        style={{ accentColor: 'var(--accent)' }}
                      />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-baseline gap-2">
                          <span className="text-[13px] font-medium text-neutral-100">
                            {env.label}
                          </span>
                          <span className="text-[10px] font-mono text-neutral-500">
                            {env.delta} lb / jump
                          </span>
                        </div>
                        <div className="text-[11px] text-neutral-500 mt-0.5">{env.blurb}</div>
                      </div>
                    </label>
                  );
                })}
              </div>
            </Field>

            <FleetEditor
              aircraft={form.aircraft}
              onChange={(next) => setForm((f) => ({ ...f, aircraft: next }))}
            />

            <Field label="NOTES">
              <textarea
                value={form.notes}
                onChange={update('notes')}
                className={`${inputCls} resize-none`}
                rows={3}
                placeholder="Optional. Anything worth remembering — runway surface, packing area, contact info."
              />
            </Field>
          </div>

          <div
            className="flex items-center gap-2 px-5 py-3"
            style={{ background: 'var(--surface-1)', borderTop: '0.5px solid var(--border-strong)' }}
          >
            <span className="text-[11px] text-neutral-500">
              {isEdit
                ? 'Saves to dropzones/<id>.xml.'
                : 'Validated against SCHEMA.v1.xsd before write.'}
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
              type="submit"
              disabled={submitting}
              className="px-3.5 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition disabled:opacity-50"
              style={{ background: 'var(--text)', color: 'var(--bg)' }}
            >
              {submitting ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  Saving…
                </>
              ) : (
                <>
                  <Plus className="w-3.5 h-3.5" strokeWidth={2.2} />
                  {isEdit ? 'Save changes' : 'Save dropzone'}
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </>
  );
}

const inputCls =
  'w-full rounded-md px-3 py-1.5 text-[13px] text-neutral-100 bg-[var(--bg)] border border-[var(--border-strong)] focus:border-[#3a3d41] transition outline-none';

function FormGrid({ children }) {
  return <div className="grid grid-cols-2 gap-3">{children}</div>;
}

function Field({ label, required, children }) {
  return (
    <label className="block">
      <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium mb-1.5">
        {label} {required && <span className="text-neutral-300">*</span>}
      </div>
      {children}
    </label>
  );
}

// --------------------------------------------------------------------- //
// FleetEditor — chip-style aircraft list (R.D.6, D44 amend)
// --------------------------------------------------------------------- //
//
// Each row holds a required model + optional tail number. The "Add
// plane" button appends an empty row; the trash icon removes one.
// Empty rows are filtered out by buildPayload so the user can leave
// a half-filled row hanging without it polluting the on-disk record.

function FleetEditor({ aircraft, onChange }) {
  function setRow(idx, key, value) {
    onChange(
      aircraft.map((p, i) => (i === idx ? { ...p, [key]: value } : p)),
    );
  }
  function addRow() {
    onChange([...aircraft, { model: '', tail_number: '' }]);
  }
  function removeRow(idx) {
    onChange(aircraft.filter((_, i) => i !== idx));
  }

  return (
    <div>
      <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium mb-1.5">
        AIRCRAFT FLEET
      </div>
      <div className="space-y-1.5">
        {aircraft.length === 0 && (
          <div className="text-[12px] text-neutral-500 italic">
            Optional. List the planes flown at this DZ — surfaces as
            suggestions on the jump form.
          </div>
        )}
        {aircraft.map((p, i) => (
          <div
            key={i}
            className="flex items-center gap-1.5 px-2 py-1.5 rounded-md"
            style={{ background: 'var(--bg)', border: '0.5px solid var(--border-strong)' }}
          >
            <Plane
              className="w-3 h-3 text-neutral-500 flex-shrink-0"
              strokeWidth={1.6}
            />
            <input
              type="text"
              value={p.model}
              onChange={(e) => setRow(i, 'model', e.target.value)}
              maxLength={120}
              placeholder="Model (e.g. Twin Otter)"
              className="flex-1 min-w-0 bg-transparent text-[13px] text-neutral-100 outline-none"
            />
            <input
              type="text"
              value={p.tail_number}
              onChange={(e) => setRow(i, 'tail_number', e.target.value)}
              maxLength={32}
              placeholder="Tail # (optional)"
              className="w-32 bg-transparent text-[12px] text-neutral-300 font-mono outline-none border-l border-[var(--border-strong)] pl-2"
            />
            <button
              type="button"
              onClick={() => removeRow(i)}
              className="w-6 h-6 rounded transition flex items-center justify-center hover:bg-neutral-800 flex-shrink-0"
              title="Remove"
            >
              <Trash2 className="w-3 h-3 text-neutral-500" strokeWidth={1.8} />
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={addRow}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[12px] text-neutral-300 transition hover:bg-neutral-800/50"
          style={{ background: 'transparent', border: '0.5px dashed var(--text-faint)' }}
        >
          <Plus className="w-3 h-3" />
          Add plane
        </button>
      </div>
    </div>
  );
}

function ErrorBanner({ error }) {
  const isApi = error instanceof ApiError;
  const problem = isApi ? error.problem : null;
  const fieldErrors = problem?.errors || [];
  return (
    <div
      className="m-5 mb-0 p-4 rounded-xl flex items-start gap-3"
      style={{
        background: 'rgba(248,113,113,0.05)',
        border: '0.5px solid rgba(248,113,113,0.25)',
      }}
    >
      <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: 'var(--status-critical)' }} />
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-medium text-neutral-100">
          {isApi ? (problem?.title || 'Validation failed') : "Couldn't save"}
        </div>
        {problem?.detail && (
          <div className="text-[12px] text-neutral-400 mt-1">{problem.detail}</div>
        )}
        {!isApi && (
          <div className="text-[12px] text-neutral-400 mt-1">{error.message}</div>
        )}
        {fieldErrors.length > 0 && (
          <ul className="mt-2 space-y-0.5">
            {fieldErrors.map((fe, i) => (
              <li key={i} className="text-[11px] text-neutral-500 font-mono">
                <span className="text-neutral-400">{fe.pointer}</span>: {fe.detail}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
