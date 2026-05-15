import React, { useState } from 'react';
import { Plane, Plus, Trash2 } from 'lucide-react';
import { createDropzone, starDropzone } from '../../api';
import { Field, FormGrid, Section, inputCls, ErrorBanner } from './formAtoms';
import { StepHeader, StepFooter } from './StepFrame';

// D44 environment enum (mirrors DropzoneModal). Order matches the
// D45 wear-math escalation: clean grass adds nothing, dust/sand/salt
// adds 0.20 lb/jump, desert adds 0.25.
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


export default function DropzoneStep({ onSubmit, onSkip, onBack }) {
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

  const update = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  // ``country`` is a strict 2-letter ISO 3166-1 alpha-2 per the
  // Dropzone model; the rest are free-text. Required fields are
  // name + city + country + environment, so Continue gates on
  // those four to give the user an immediate cue rather than a
  // 422 round-trip.
  const canContinue = Boolean(
    form.name.trim()
    && form.city.trim()
    && form.country.trim().length === 2
    && form.environment,
  );

  async function handleContinue() {
    if (submitting || !canContinue) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createDropzone({
        name: form.name.trim(),
        city: form.city.trim(),
        province: form.province.trim() || null,
        country: form.country.trim().toUpperCase(),
        environment: form.environment,
        // Drop empty model rows; trim each field; collapse blank
        // tail numbers to null so the backend writes
        // ``<tail_number>`` only when the user actually filled it.
        aircraft: form.aircraft
          .map((p) => ({
            model: (p.model || '').trim(),
            tail_number: (p.tail_number || '').trim() || null,
          }))
          .filter((p) => p.model.length > 0),
        notes: form.notes.trim() || null,
      });

      // D60: the wizard's "home dropzone" step is semantically the
      // user's default — explicitly star it so the LogJumpModal
      // prefills it on every new jump. The server-side auto-star on
      // creation only fires for the very first DZ in a fresh
      // logbook; users who resume the wizard with prior DZs
      // wouldn't otherwise get their new home DZ as the default.
      // Best-effort: a star failure doesn't roll the create back —
      // the DZ exists either way, the user can star it manually
      // from the Dropzones tab.
      try {
        const starred = await starDropzone(created.id);
        onSubmit(starred);
      } catch {
        onSubmit(created);
      }
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
          label="STEP 2 OF 7"
          title="Your home dropzone"
          blurb="The DZ you jump from most often. Environment feeds line-wear projections; you can add more dropzones from the Dropzones tab later."
        />

        {error && <ErrorBanner error={error} />}

        <div className="space-y-4">
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
                placeholder="optional"
              />
            </Field>
          </FormGrid>

          <Field label="COUNTRY (ISO 2-letter)" required>
            <input
              type="text"
              required
              value={form.country}
              onChange={update('country')}
              onBlur={(e) =>
                setForm({ ...form, country: e.target.value.toUpperCase() })
              }
              className={inputCls}
              placeholder="CA, US, FR…"
              maxLength={2}
              pattern="[A-Za-z]{2}"
              style={{ textTransform: 'uppercase' }}
            />
          </Field>

          <Section label="JUMPING ENVIRONMENT">
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
                      <div className="text-[11px] text-neutral-500 mt-0.5">
                        {env.blurb}
                      </div>
                    </div>
                  </label>
                );
              })}
            </div>
          </Section>

          <FleetEditor
            aircraft={form.aircraft}
            onChange={(next) => setForm({ ...form, aircraft: next })}
          />

          <Field label="NOTES">
            <textarea
              value={form.notes}
              onChange={update('notes')}
              className={inputCls}
              rows={3}
              placeholder="Anything worth remembering — opening hours, packing-mat etiquette, manifest quirks."
              style={{ resize: 'vertical' }}
            />
          </Field>
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


// FleetEditor — chip-style aircraft list. Mirrors the
// implementation in DropzoneModal but inlined here so the wizard
// step is self-contained; the modal copy keeps the regular DZ-edit
// surface working unchanged.
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
    <Section label="AIRCRAFT FLEET">
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
            style={{
              background: 'var(--bg)',
              border: '0.5px solid var(--border-strong)',
            }}
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
          style={{
            background: 'transparent',
            border: '0.5px dashed var(--text-faint)',
          }}
        >
          <Plus className="w-3 h-3" />
          Add plane
        </button>
      </div>
    </Section>
  );
}
