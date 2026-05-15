import React, { useState } from 'react';
import { createMain } from '../../api';
import { LINE_MATERIALS, composeLineType } from '../../lineTypes';
import { Field, FormGrid, Section, inputCls, ErrorBanner } from './formAtoms';
import { StepHeader, StepFooter } from './StepFrame';

// Heaviest step in the wizard — main canopy carries its own optional
// lineset block (D38 used-gear onboarding). The lineset half is
// collapsed by default behind a "Show lineset fields" toggle so a
// brand-new jumper on factory lines isn't faced with a wall of
// material/variant/strength fields they may not know.
//
// The MainCreate model requires nothing here (size_sqft is optional
// at create time, defaults to null on the wire), but Continue softly
// suggests size_sqft because the wear math is meaningless without it
// — the user can still skip with an empty form via Skip.
export default function MainStep({ onSubmit, onSkip, onBack }) {
  const [form, setForm] = useState({
    manufacturer: '',
    model: '',
    serial: '',
    date_of_manufacture: '',
    size_sqft: '',
    jump_count_initial: '0',
    has_rds: false,
    // lineset block — open by default since a new jumper almost
    // always knows their line material; backing it behind a toggle
    // hid the most relevant data D45 needs. Leaving material blank
    // still produces a main "not yet lined" (line_type composes to
    // null → current_lineset is omitted).
    line_material: '',
    line_variant: '',
    breaking_strength_lb: '',
    install_date: new Date().toISOString().slice(0, 10),
    installed_by: '',
    jumps_on_lineset_initial: '0',
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const update = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  async function handleContinue() {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const numOrZero = (v) => {
        const n = parseInt(v, 10);
        return Number.isFinite(n) ? n : 0;
      };
      const floatOrNull = (v) => {
        if (v === '' || v == null) return null;
        const n = parseFloat(v);
        return Number.isFinite(n) ? n : null;
      };

      const payload = {
        status: 'active',
        manufacturer: form.manufacturer.trim() || null,
        model: form.model.trim() || null,
        serial: form.serial.trim() || null,
        date_of_manufacture: form.date_of_manufacture || null,
        size_sqft: floatOrNull(form.size_sqft),
        has_rds: !!form.has_rds,
        jump_count_initial: numOrZero(form.jump_count_initial),
      };

      // Per D38: include current_lineset only when the user chose
      // a material + variant. Material blank → main is "not yet
      // lined" and ``current_lineset`` is omitted; the wear math
      // (D45) falls back to the canopy's default flags.
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

      const created = await createMain(payload);
      onSubmit(created);
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <div className="px-7 py-7 flex-1 overflow-y-auto">
        <StepHeader
          label="STEP 4 OF 7"
          title="Your main canopy"
          blurb="The canopy you'll typically jump. Size feeds wingloading; lineset feeds wear math — both can be added later from Inventory."
        />

        {error && <ErrorBanner error={error} />}

        <div className="space-y-3">
          <Section label="IDENTIFICATION">
            <FormGrid>
              <Field label="MANUFACTURER">
                <input
                  value={form.manufacturer}
                  onChange={update('manufacturer')}
                  placeholder="e.g. Performance Designs"
                  className={inputCls}
                />
              </Field>
              <Field label="MODEL">
                <input
                  value={form.model}
                  onChange={update('model')}
                  placeholder="e.g. Sabre 3"
                  className={inputCls}
                />
              </Field>
            </FormGrid>
            <FormGrid>
              <Field label="SERIAL">
                <input
                  value={form.serial}
                  onChange={update('serial')}
                  placeholder="manufacturer SN"
                  className={inputCls}
                />
              </Field>
              <Field label="DATE OF MANUFACTURE">
                <input
                  type="date"
                  value={form.date_of_manufacture}
                  onChange={update('date_of_manufacture')}
                  className={inputCls}
                />
              </Field>
            </FormGrid>
          </Section>

          <Section label="GEOMETRY & COUNTERS">
            <FormGrid>
              <Field label="SIZE (sqft)">
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  value={form.size_sqft}
                  onChange={update('size_sqft')}
                  placeholder="e.g. 170"
                  className={inputCls}
                />
              </Field>
              <Field label="JUMP COUNT (initial)">
                <input
                  type="number"
                  min="0"
                  value={form.jump_count_initial}
                  onChange={update('jump_count_initial')}
                  placeholder="0 for new gear"
                  className={inputCls}
                />
              </Field>
            </FormGrid>
            <label className="flex items-start gap-2 mt-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={form.has_rds}
                onChange={(e) => setForm({ ...form, has_rds: e.target.checked })}
                className="mt-0.5 w-3.5 h-3.5 rounded"
                style={{ accentColor: 'var(--status-ready)' }}
              />
              <span className="flex-1">
                <span className="block text-[12px] text-neutral-200">
                  Removable deployment system (RDS)
                </span>
                <span className="block text-[10px] text-neutral-500 mt-0.5">
                  Adds +0.15 lb of line wear per jump (D45). Check if this
                  canopy has a removable slider / collapsible pilot chute setup.
                </span>
              </span>
            </label>
          </Section>

          <Section label="CURRENT LINESET">
            <div className="text-[11px] text-neutral-500 leading-relaxed mb-2">
              Optional. Pick a material to record what's currently on
              your canopy — drives D45's wear math. Leave the material
              blank if you don't know; you can add a lineset any time
              from the Inventory tab.
            </div>
            <div
              className="rounded-lg p-3 mt-2"
              style={{
                background: 'var(--surface-2)',
                border: '0.5px solid var(--border)',
              }}
            >
              <FormGrid>
                  <Field label="MATERIAL">
                    <select
                      value={form.line_material}
                      onChange={(e) => {
                        const m = e.target.value;
                        setForm({ ...form, line_material: m, line_variant: '' });
                      }}
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
                        onChange={update('line_variant')}
                        placeholder="custom line description"
                        className={inputCls}
                      />
                    ) : (
                      <select
                        value={form.line_variant}
                        onChange={(e) => {
                          const v = e.target.value;
                          const variants = LINE_MATERIALS[form.line_material]?.variants || [];
                          const found = variants.find((x) => x.value === v);
                          setForm({
                            ...form,
                            line_variant: v,
                            breaking_strength_lb: found ? String(found.strength) : form.breaking_strength_lb,
                          });
                        }}
                        disabled={!form.line_material}
                        className={inputCls}
                      >
                        <option value="">— pick a variant —</option>
                        {(LINE_MATERIALS[form.line_material]?.variants || []).map((x) => (
                          <option key={x.value} value={x.value}>
                            {form.line_material === 'vectran'
                              ? x.value
                              : `${LINE_MATERIALS[form.line_material].label} ${x.value}`}
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
                      onChange={update('breaking_strength_lb')}
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
                      onChange={update('install_date')}
                      className={inputCls}
                    />
                  </Field>
                  <Field label="INSTALLED BY">
                    <input
                      value={form.installed_by}
                      onChange={update('installed_by')}
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
                      onChange={update('jumps_on_lineset_initial')}
                      placeholder="0 for fresh install"
                      className={inputCls}
                    />
                  </Field>
                  <div />
                </FormGrid>
            </div>
          </Section>
        </div>
      </div>

      <StepFooter
        onBack={onBack}
        onSkip={onSkip}
        onContinue={handleContinue}
        continueLabel="Save & continue"
        submitting={submitting}
      />
    </>
  );
}
