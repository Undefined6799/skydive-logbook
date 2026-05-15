import React, { useState } from 'react';
import { createContainer } from '../../api';
import { Field, FormGrid, Section, inputCls, ErrorBanner } from './formAtoms';
import { StepHeader, StepFooter } from './StepFrame';

// Mirrors AddComponentModal's container branch, minus the "kind"
// switcher (this step is always a container). Every field is optional
// per the ContainerCreate model (only ``status`` is required and is
// hard-coded to "active") so the user can skip ahead with empty
// fields if they want a placeholder. Continue calls
// ``createContainer``; Skip advances without a POST.
export default function ContainerStep({ onSubmit, onSkip, onBack }) {
  const [form, setForm] = useState({
    manufacturer: '',
    model: '',
    serial: '',
    date_of_manufacture: '',
    size: '',
    jump_count_initial: '0',
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
      const created = await createContainer({
        status: 'active',
        manufacturer: form.manufacturer.trim() || null,
        model: form.model.trim() || null,
        serial: form.serial.trim() || null,
        date_of_manufacture: form.date_of_manufacture || null,
        size: form.size.trim() || null,
        jump_count_initial: numOrZero(form.jump_count_initial),
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
          label="STEP 3 OF 7"
          title="Your container"
          blurb="The harness / container system that holds your main and reserve. All fields are optional — you can edit any of them later from the Inventory tab."
        />

        {error && <ErrorBanner error={error} />}

        <div className="space-y-3">
          <Section label="IDENTIFICATION">
            <FormGrid>
              <Field label="MANUFACTURER">
                <input
                  value={form.manufacturer}
                  onChange={update('manufacturer')}
                  placeholder="e.g. United Parachute Technologies"
                  className={inputCls}
                />
              </Field>
              <Field label="MODEL">
                <input
                  value={form.model}
                  onChange={update('model')}
                  placeholder="e.g. Vector V348"
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
              <Field label="SIZE (free text)">
                <input
                  value={form.size}
                  onChange={update('size')}
                  placeholder='e.g. "M22", "Large"'
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
