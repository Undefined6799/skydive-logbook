import React, { useState } from 'react';
import { createAad } from '../../api';
import { Field, FormGrid, Section, inputCls, ErrorBanner } from './formAtoms';
import { StepHeader, StepFooter } from './StepFrame';

// AAD step — identification + mode + counters. The mode-changeable
// dropdown matches AddComponentModal's three-way (yes/no/unknown).
// D39's per-brand recertification rules read from
// (manufacturer, model, DOM) at display time, so the wizard doesn't
// need any AAD-specific validation — pass the strings through and
// let the Inventory tab surface the recert calendar on next view.
export default function AadStep({ onSubmit, onSkip, onBack }) {
  const [form, setForm] = useState({
    manufacturer: '',
    model: '',
    serial: '',
    date_of_manufacture: '',
    mode: '',
    is_changeable_mode: '',
    jump_count_initial: '0',
    fire_count_initial: '0',
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
      const created = await createAad({
        status: 'active',
        manufacturer: form.manufacturer.trim() || null,
        model: form.model.trim() || null,
        serial: form.serial.trim() || null,
        date_of_manufacture: form.date_of_manufacture || null,
        mode: form.mode.trim() || null,
        is_changeable_mode:
          form.is_changeable_mode === 'true' ? true
          : form.is_changeable_mode === 'false' ? false
          : null,
        jump_count_initial: numOrZero(form.jump_count_initial),
        fire_count_initial: numOrZero(form.fire_count_initial),
      });
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
          label="STEP 6 OF 7"
          title="Your AAD"
          blurb="Automatic activation device — Cypres, Vigil, MARS. Manufacturer + model + date of manufacture together drive the recertification calendar (D39)."
        />

        {error && <ErrorBanner error={error} />}

        <div className="space-y-3">
          <Section label="IDENTIFICATION">
            <FormGrid>
              <Field label="MANUFACTURER">
                <input
                  value={form.manufacturer}
                  onChange={update('manufacturer')}
                  placeholder="e.g. Airtec"
                  className={inputCls}
                />
              </Field>
              <Field label="MODEL">
                <input
                  value={form.model}
                  onChange={update('model')}
                  placeholder="e.g. Cypres 2"
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

          <Section label="MODE & COUNTERS">
            <FormGrid>
              <Field label="MODE">
                <input
                  value={form.mode}
                  onChange={update('mode')}
                  placeholder="e.g. Pro / Expert / Tandem"
                  className={inputCls}
                />
              </Field>
              <Field label="MODE CHANGEABLE">
                <select
                  value={form.is_changeable_mode}
                  onChange={update('is_changeable_mode')}
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
                  onChange={update('jump_count_initial')}
                  placeholder="0 for new gear"
                  className={inputCls}
                />
              </Field>
              <Field label="FIRE COUNT (initial)">
                <input
                  type="number"
                  min="0"
                  value={form.fire_count_initial}
                  onChange={update('fire_count_initial')}
                  placeholder="0 for new gear"
                  className={inputCls}
                />
              </Field>
            </FormGrid>
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
