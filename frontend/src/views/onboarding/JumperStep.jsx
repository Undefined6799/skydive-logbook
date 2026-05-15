import React, { useState } from 'react';
import { createJumper, addJumperMembership, addJumperCop } from '../../api';
import { Field, FormGrid, Section, inputCls, ErrorBanner } from './formAtoms';
import { StepHeader, StepFooter } from './StepFrame';

// Step 2: Your profile (name, exit weight, optional credentials).
//
// Mirrors Profile.jsx's existing OnboardingForm for the identity
// half (name + exit_weight) and extends it with a single license
// + single membership card so a typical jumper can capture their
// USPA-A / CSPA-B / etc. without leaving the wizard. More
// elaborate credential editing (multiple ratings, tandem ratings,
// medicals, card attachments) lives on the Profile tab — D47's
// dedicated endpoints already power that surface.
//
// Submit order, all under a single "Save & continue" click:
//   1. POST /jumpers           — identity (required for the rest)
//   2. POST /jumpers/{id}/memberships   — only if user filled it
//   3. POST /jumpers/{id}/cops          — only if user filled it
// A failure on (2)/(3) leaves the jumper present and surfaces a
// non-fatal error; the user can fill credentials from Profile.

const FED_OPTIONS = [
  { value: 'USPA',  label: 'USPA' },
  { value: 'CSPA',  label: 'CSPA' },
  { value: 'OTHER', label: 'Other' },
];

// Backend enums (jumper.py) use lowercase letters for license
// levels — CSPACopLevel includes `solo` as a pre-A level. The wire
// values match these; the UI uppercases them on display only.
const COP_LEVELS = {
  USPA: ['a', 'b', 'c', 'd'],
  CSPA: ['solo', 'a', 'b', 'c', 'd'],
  OTHER: [],
};


export default function JumperStep({ onSubmit, onSkip, onBack }) {
  const [form, setForm] = useState({
    name: '',
    exit_weight_lb: '',
    // membership card (optional)
    add_membership: false,
    membership_fed: 'USPA',
    membership_fed_other: '',
    membership_number: '',
    membership_expiry: '',
    // license / CoP (optional). Level value is the wire format
    // (lowercase) per the backend enum; the dropdown UPPERCASES
    // it for display below.
    add_cop: false,
    cop_fed: 'USPA',
    cop_fed_other: '',
    cop_level: 'a',
    cop_issued: '',
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const update = (key) => (e) => setForm({ ...form, [key]: e.target.value });
  const toggle = (key) => (e) => setForm({ ...form, [key]: e.target.checked });

  const exitWeightNum = parseFloat(form.exit_weight_lb);
  const canContinue = Number.isFinite(exitWeightNum) && exitWeightNum > 0;

  async function handleContinue() {
    if (submitting || !canContinue) return;
    setSubmitting(true);
    setError(null);
    try {
      // Step 1 — identity. JumperCreate forbids extra fields, so
      // credentials are layered on after the jumper exists.
      const jumper = await createJumper({
        name: form.name.trim() || null,
        exit_weight_lb: exitWeightNum,
      });

      // Step 2 — membership (best-effort).
      if (form.add_membership && form.membership_number.trim() && form.membership_expiry) {
        try {
          const fed = form.membership_fed === 'OTHER'
            ? form.membership_fed_other.trim() || 'OTHER'
            : form.membership_fed;
          await addJumperMembership(jumper.id, {
            org: form.membership_fed,
            org_other: form.membership_fed === 'OTHER' ? fed : null,
            member_number: form.membership_number.trim(),
            expiry_date: form.membership_expiry,
          });
        } catch (membershipErr) {
          // Non-fatal — surface to the wizard's error slot but
          // still advance, since the jumper exists.
          setError(membershipErr);
        }
      }

      // Step 3 — CoP / license (best-effort).
      if (form.add_cop && form.cop_level && form.cop_issued) {
        try {
          await addJumperCop(jumper.id, {
            org: form.cop_fed,
            org_other: form.cop_fed === 'OTHER'
              ? (form.cop_fed_other.trim() || 'OTHER')
              : null,
            level: form.cop_level,
            issued_date: form.cop_issued,
          });
        } catch (copErr) {
          setError(copErr);
        }
      }

      onSubmit(jumper);
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
          label="STEP 2 OF 8"
          title="Your profile"
          blurb="A few details so your logbook can attribute every jump correctly. You can edit these any time from the Profile tab."
        />

        {error && <ErrorBanner error={error} />}

        <div className="space-y-4">
          <FormGrid>
            <Field label="NAME">
              <input
                value={form.name}
                onChange={update('name')}
                placeholder="optional — e.g. Alex"
                className={inputCls}
                maxLength={120}
              />
            </Field>
            <Field label="EXIT WEIGHT (lb)" required>
              <input
                type="number"
                step="0.1"
                min="0.1"
                value={form.exit_weight_lb}
                onChange={update('exit_weight_lb')}
                placeholder="all-up: body + rig + clothing"
                className={inputCls}
              />
            </Field>
          </FormGrid>

          <Section label="MEMBERSHIP (optional)">
            <label className="flex items-start gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={form.add_membership}
                onChange={toggle('add_membership')}
                className="mt-0.5 w-3.5 h-3.5 rounded"
                style={{ accentColor: 'var(--status-ready)' }}
              />
              <span className="flex-1">
                <span className="block text-[12px] text-neutral-200">
                  Add my federation membership
                </span>
                <span className="block text-[10px] text-neutral-500 mt-0.5">
                  USPA / CSPA member number + expiry. Skip if you don't have one yet.
                </span>
              </span>
            </label>

            {form.add_membership && (
              <div
                className="rounded-lg p-3 mt-2 space-y-3"
                style={{
                  background: 'var(--surface-2)',
                  border: '0.5px solid var(--border)',
                }}
              >
                <FormGrid>
                  <Field label="FEDERATION">
                    <select
                      value={form.membership_fed}
                      onChange={update('membership_fed')}
                      className={inputCls}
                    >
                      {FED_OPTIONS.map((f) => (
                        <option key={f.value} value={f.value}>{f.label}</option>
                      ))}
                    </select>
                  </Field>
                  {form.membership_fed === 'OTHER' && (
                    <Field label="FEDERATION NAME">
                      <input
                        value={form.membership_fed_other}
                        onChange={update('membership_fed_other')}
                        placeholder="e.g. APF, BPA"
                        className={inputCls}
                      />
                    </Field>
                  )}
                </FormGrid>
                <FormGrid>
                  <Field label="MEMBER NUMBER">
                    <input
                      value={form.membership_number}
                      onChange={update('membership_number')}
                      placeholder="from your membership card"
                      className={inputCls}
                    />
                  </Field>
                  <Field label="EXPIRY DATE">
                    <input
                      type="date"
                      value={form.membership_expiry}
                      onChange={update('membership_expiry')}
                      className={inputCls}
                    />
                  </Field>
                </FormGrid>
              </div>
            )}
          </Section>

          <Section label="LICENSE / COP (optional)">
            <label className="flex items-start gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={form.add_cop}
                onChange={toggle('add_cop')}
                className="mt-0.5 w-3.5 h-3.5 rounded"
                style={{ accentColor: 'var(--status-ready)' }}
              />
              <span className="flex-1">
                <span className="block text-[12px] text-neutral-200">
                  Add my license
                </span>
                <span className="block text-[10px] text-neutral-500 mt-0.5">
                  USPA A/B/C/D or CSPA A/B/C/D Certificate of Proficiency.
                </span>
              </span>
            </label>

            {form.add_cop && (
              <div
                className="rounded-lg p-3 mt-2 space-y-3"
                style={{
                  background: 'var(--surface-2)',
                  border: '0.5px solid var(--border)',
                }}
              >
                <FormGrid>
                  <Field label="FEDERATION">
                    <select
                      value={form.cop_fed}
                      onChange={(e) => {
                        const fed = e.target.value;
                        // When switching federation, reset level to
                        // the new federation's first valid level so
                        // an invalid combination can't be submitted.
                        const validLevels = COP_LEVELS[fed] || [];
                        setForm({
                          ...form,
                          cop_fed: fed,
                          cop_level: validLevels[0] || form.cop_level,
                        });
                      }}
                      className={inputCls}
                    >
                      {FED_OPTIONS.map((f) => (
                        <option key={f.value} value={f.value}>{f.label}</option>
                      ))}
                    </select>
                  </Field>
                  {form.cop_fed === 'OTHER' && (
                    <Field label="FEDERATION NAME">
                      <input
                        value={form.cop_fed_other}
                        onChange={update('cop_fed_other')}
                        placeholder="e.g. APF, BPA"
                        className={inputCls}
                      />
                    </Field>
                  )}
                </FormGrid>
                <FormGrid>
                  <Field label="LEVEL">
                    {form.cop_fed === 'OTHER' ? (
                      <input
                        value={form.cop_level}
                        onChange={update('cop_level')}
                        placeholder="free text"
                        className={inputCls}
                      />
                    ) : (
                      <select
                        value={form.cop_level}
                        onChange={update('cop_level')}
                        className={inputCls}
                      >
                        {(COP_LEVELS[form.cop_fed] || []).map((lvl) => (
                          <option key={lvl} value={lvl}>
                            {lvl === 'solo' ? 'Solo' : lvl.toUpperCase()}
                          </option>
                        ))}
                      </select>
                    )}
                  </Field>
                  <Field label="ISSUED DATE">
                    <input
                      type="date"
                      value={form.cop_issued}
                      onChange={update('cop_issued')}
                      className={inputCls}
                    />
                  </Field>
                </FormGrid>
              </div>
            )}
          </Section>

          <div className="text-[11px] text-neutral-500 leading-relaxed">
            Ratings (Coach, AFFI, Rigger…), tandem ratings, and medicals
            live on the Profile tab — they have their own validation
            rules and date pickers, so the wizard keeps to the basics.
          </div>
        </div>
      </div>

      <StepFooter
        onBack={onBack}
        onSkip={onSkip}
        onContinue={handleContinue}
        continueLabel="Save & continue"
        submitting={submitting}
        canContinue={canContinue}
      />
    </>
  );
}
