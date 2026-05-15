import React, { useState } from 'react';
import { createReserve } from '../../api';
import { Field, FormGrid, Section, inputCls, ErrorBanner } from './formAtoms';
import { StepHeader, StepFooter } from './StepFrame';

// Mirrors AddComponentModal's reserve branch. ReserveCreate has no
// strictly-required fields; every limit / counter / identification
// field is optional and can be edited later. The repack/ride limits
// and counts come from the jumper's logbook (used-gear onboarding
// path D38). recert_extensions are deferred to the Inventory tab —
// they're a list with their own date pickers that doesn't fit the
// linear "fill one form" wizard shape.
export default function ReserveStep({ onSubmit, onSkip, onBack }) {
  const [form, setForm] = useState({
    manufacturer: '',
    model: '',
    serial: '',
    date_of_manufacture: '',
    size_sqft: '',
    repack_limit: '',
    ride_limit: '',
    repack_count_initial: '0',
    ride_count_initial: '0',
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

      const created = await createReserve({
        status: 'active',
        manufacturer: form.manufacturer.trim() || null,
        model: form.model.trim() || null,
        serial: form.serial.trim() || null,
        date_of_manufacture: form.date_of_manufacture || null,
        size_sqft: floatOrNull(form.size_sqft),
        repack_limit: numOrNull(form.repack_limit),
        ride_limit: numOrNull(form.ride_limit),
        repack_count_initial: numOrZero(form.repack_count_initial),
        ride_count_initial: numOrZero(form.ride_count_initial),
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
          label="STEP 5 OF 7"
          title="Your reserve canopy"
          blurb="The canopy under your reserve pin. Repack limits and ride counts drive D38's repack-due clock and the reserve-currency widgets on My Rig."
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
                  placeholder="e.g. Optimum"
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
                  placeholder="e.g. 143"
                  className={inputCls}
                />
              </Field>
              <div />
            </FormGrid>
            <FormGrid>
              <Field label="REPACK COUNT (initial)">
                <input
                  type="number"
                  min="0"
                  value={form.repack_count_initial}
                  onChange={update('repack_count_initial')}
                  placeholder="0 for new gear"
                  className={inputCls}
                />
              </Field>
              <Field label="RIDE COUNT (initial)">
                <input
                  type="number"
                  min="0"
                  value={form.ride_count_initial}
                  onChange={update('ride_count_initial')}
                  placeholder="0 for new gear"
                  className={inputCls}
                />
              </Field>
            </FormGrid>
            <FormGrid>
              <Field label="REPACK LIMIT">
                <input
                  type="number"
                  min="0"
                  value={form.repack_limit}
                  onChange={update('repack_limit')}
                  placeholder="manufacturer spec"
                  className={inputCls}
                />
              </Field>
              <Field label="RIDE LIMIT">
                <input
                  type="number"
                  min="0"
                  value={form.ride_limit}
                  onChange={update('ride_limit')}
                  placeholder="manufacturer spec"
                  className={inputCls}
                />
              </Field>
            </FormGrid>
          </Section>

          <div className="text-[11px] text-neutral-500 leading-relaxed">
            Recertification extensions (re-pull and re-line records) are
            entered from the Inventory tab — those have their own date
            picker and don't fit the linear wizard form. You can do that
            any time after setup.
          </div>
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
