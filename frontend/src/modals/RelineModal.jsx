import React, { useEffect, useState } from 'react';
import { X, Loader2, AlertTriangle, Save } from 'lucide-react';
import { updateMain, ApiError } from '../api';
import { LINE_MATERIALS, composeLineType } from '../lineTypes';

// Reline modal (R.1.b). Captures the new lineset metadata after a
// rigger has installed fresh lines on a main canopy. On submit:
//
//   * The existing ``current_lineset`` (if any) is APPENDED to
//     ``lineset_history`` with its id preserved (D36 — historical
//     jump rig-snapshots that pin to that id stay valid; the lineset
//     becomes archived but addressable).
//   * A fresh ``current_lineset`` is constructed from the form
//     fields. Pydantic's default_factory mints a new UUID server-
//     side because we omit ``id`` from the payload — that new id
//     becomes the pin target for jumps logged after this reline.
//   * The PUT body is a full-replace MainUpdate; every other field
//     on the on-disk main is echoed verbatim so notes_log and
//     counters round-trip cleanly.
//
// The reline workflow was deferred from R.5 to here as a pragmatic
// v0.1 step — the user needs a way to record fresh lines NOW, and
// the existing PUT surface already supports lineset_history shifts.
// A future R.5 dedicated endpoint (``POST /mains/{id}/reline`` with
// the same payload shape) can ride atop this UX without changing
// the modal.

export default function RelineModal({ main, onClose, onRelined }) {
  const [form, setForm] = useState(emptyForm);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!main) {
      document.body.style.overflow = '';
      return;
    }
    document.body.style.overflow = 'hidden';
    setForm(emptyForm());
    setError(null);
    setSubmitting(false);
    return () => { document.body.style.overflow = ''; };
  }, [main]);

  if (!main) return null;

  function update(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function handleSubmit(e) {
    e?.preventDefault?.();
    setSubmitting(true);
    setError(null);
    try {
      const composed = composeLineType(form.line_material, form.line_variant);
      if (!composed) {
        throw new Error('Pick a material and variant before saving.');
      }
      const newLineset = {
        // id omitted on purpose — Pydantic's default_factory mints
        // a fresh UUID. Per D36 this is the new pin target for
        // future jump rig-snapshots.
        line_type: composed,
        breaking_strength_lb: parseFloat(form.breaking_strength_lb),
        install_date: form.install_date,
        installed_by: form.installed_by.trim() || null,
        jumps_on_lineset_initial: parseInt(form.jumps_on_lineset_initial, 10) || 0,
      };

      // Move the old current_lineset (if any) to the END of the
      // archive list, preserving its id. The order is install
      // chronology — newest archived last.
      const oldHistory = Array.isArray(main.lineset_history) ? main.lineset_history : [];
      const newHistory = main.current_lineset
        ? [...oldHistory, main.current_lineset]
        : oldHistory;

      // Build a full-replace MainUpdate body. Strip server-managed
      // fields (id, assigned_rig_id, created_at, updated_at) per
      // ComponentDetailModal's buildUpdatePayload posture.
      const stripped = { ...main };
      delete stripped.id;
      delete stripped.assigned_rig_id;
      delete stripped.created_at;
      delete stripped.updated_at;

      const payload = {
        ...stripped,
        current_lineset: newLineset,
        lineset_history: newHistory,
      };

      await updateMain(main.id, payload);
      if (onRelined) onRelined();
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
        className="fixed inset-0 z-[60]"
        style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)' }}
      />
      <div className="fixed inset-0 z-[70] flex items-start justify-center p-6 pointer-events-none overflow-y-auto">
        <form
          onSubmit={handleSubmit}
          onClick={(e) => e.stopPropagation()}
          className="rounded-2xl w-full max-w-lg pointer-events-auto mt-16 flex flex-col"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)' }}
        >
          <div className="flex items-start justify-between px-5 pt-5 pb-3.5"
               style={{ borderBottom: '0.5px solid var(--border-strong)' }}>
            <div>
              <div className="text-[9px] tracking-[0.25em] text-neutral-500 font-medium mb-1">
                RELINE MAIN
              </div>
              <div className="text-[18px] font-medium tracking-tight">
                Install fresh lines
              </div>
              <div className="text-[11px] text-neutral-500 mt-0.5">
                The current lineset (if any) is archived to history with its
                id preserved. New jumps log against the new lineset id.
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

          <div className="p-5 space-y-3">
            <FormGrid>
              <Field label="MATERIAL">
                <select
                  value={form.line_material}
                  onChange={(e) => {
                    const m = e.target.value;
                    update('line_material', m);
                    update('line_variant', '');
                  }}
                  disabled={submitting}
                  className={inputCls}
                >
                  <option value="">— pick a material —</option>
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
                    disabled={submitting}
                    placeholder="custom line description"
                    className={inputCls}
                  />
                ) : (
                  <select
                    value={form.line_variant}
                    onChange={(e) => {
                      const v = e.target.value;
                      update('line_variant', v);
                      const variants = LINE_MATERIALS[form.line_material]?.variants || [];
                      const found = variants.find((x) => x.value === v);
                      if (found) update('breaking_strength_lb', String(found.strength));
                    }}
                    disabled={submitting || !form.line_material}
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
                  onChange={(e) => update('breaking_strength_lb', e.target.value)}
                  disabled={submitting || !form.line_material}
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
                  disabled={submitting}
                  className={inputCls}
                />
              </Field>
              <Field label="INSTALLED BY (optional)">
                <input
                  value={form.installed_by}
                  onChange={(e) => update('installed_by', e.target.value)}
                  disabled={submitting}
                  placeholder="rigger name"
                  className={inputCls}
                />
              </Field>
            </FormGrid>

            <FormGrid>
              <Field label="JUMPS ON LINESET (start)">
                <input
                  type="number"
                  step="1"
                  min="0"
                  value={form.jumps_on_lineset_initial}
                  onChange={(e) => update('jumps_on_lineset_initial', e.target.value)}
                  disabled={submitting}
                  placeholder="0 for fresh install"
                  className={inputCls}
                />
              </Field>
              <div />
            </FormGrid>

            <div className="text-[11px] text-neutral-500 leading-relaxed space-y-1">
              <div>
                <span className="text-neutral-400">Canopy total jumps</span>
                {' '}(<span className="font-mono">{main.jump_count_initial ?? 0}</span>) is preserved —
                the lineset is what's being replaced, not the canopy.
                <span className="text-neutral-400"> Jumps on lineset</span> resets to 0 for the new install.
              </div>
              {main.current_lineset ? (
                <div>
                  The existing <span className="font-mono">{main.current_lineset.line_type}</span> lineset
                  ({main.current_lineset.jumps_on_lineset_initial ?? 0} jumps recorded) will move to history
                  with its id preserved (D36) so any logged jumps that pin to it stay valid.
                </div>
              ) : (
                <div>This canopy has no current lineset — this is the first install.</div>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2 px-5 py-3"
               style={{ background: 'var(--surface-1)', borderTop: '0.5px solid var(--border-strong)' }}>
            <span className="text-[11px] text-neutral-500">
              Reline writes a new <span className="font-mono">current_lineset</span> with a fresh id.
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
              disabled={
                submitting
                || !form.line_material
                || !form.line_variant
                || !form.breaking_strength_lb
                || !form.install_date
              }
              className="px-3.5 py-1.5 rounded-md text-[12px] font-medium transition flex items-center gap-1.5"
              style={{
                background: submitting ? 'var(--surface-3)' : 'var(--text)',
                color: submitting ? 'var(--text-faint)' : 'var(--bg)',
                cursor: submitting ? 'not-allowed' : 'pointer',
              }}
            >
              {submitting ? (
                <>
                  <Loader2 className="w-3 h-3 animate-spin" />
                  Saving…
                </>
              ) : (
                <>
                  <Save className="w-3 h-3" />
                  Install lineset
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </>
  );
}


function emptyForm() {
  return {
    line_material: '',
    line_variant: '',
    breaking_strength_lb: '',
    install_date: new Date().toISOString().slice(0, 10),
    installed_by: '',
    jumps_on_lineset_initial: '0',
  };
}


function ErrorBanner({ error }) {
  const isApi = error instanceof ApiError;
  const problem = isApi ? error.problem : null;
  const pointers = problem?.errors || [];
  return (
    <div
      className="px-5 py-2.5 text-[12px]"
      style={{ background: 'rgba(248,113,113,0.06)', color: 'var(--status-critical)', borderBottom: '0.5px solid var(--border-strong)' }}
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className="w-3.5 h-3.5 mt-0.5" />
        <div className="flex-1 min-w-0">
          <div>{problem?.detail || error.message || String(error)}</div>
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
  'w-full rounded-md px-3 py-1.5 text-[13px] text-neutral-100 bg-[var(--bg)] border border-neutral-800 focus:border-neutral-600 focus:outline-none disabled:opacity-50';
