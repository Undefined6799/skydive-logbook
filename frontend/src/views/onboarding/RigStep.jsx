import React, { useState } from 'react';
import { CheckCircle2, AlertCircle } from 'lucide-react';
import { createRig } from '../../api';
import { Field, FormGrid, Section, inputCls, ErrorBanner } from './formAtoms';
import { StepHeader, StepFooter } from './StepFrame';

const JURISDICTION_BUTTONS = [
  { label: 'USPA', value: 'USPA' },
  { label: 'CSPA', value: 'CSPA' },
  { label: 'Both', value: 'both' },
];


// Rig step — combines the four components the user just created
// into a named rig with a jurisdiction (D33) and optionally seeds
// the repack clock from a last-repack date (D38 used-gear
// onboarding path).
//
// Renders one of two views depending on `created`:
//
//   * **All four present** — full form with the chosen component
//     IDs auto-filled. The component summary card at the top shows
//     what's about to be combined.
//   * **Some skipped** — an info card explains the rig can't be
//     built yet (D37 invariant: a rig needs exactly one of each
//     component kind), and the only forward action is "Skip rig
//     assembly". The wizard's Skip handler advances to Finish.
export default function RigStep({ created, onSubmit, onSkip, onBack }) {
  const slots = [
    { key: 'container', label: 'Container', record: created.container },
    { key: 'main',      label: 'Main',      record: created.main      },
    { key: 'reserve',   label: 'Reserve',   record: created.reserve   },
    { key: 'aad',       label: 'AAD',       record: created.aad       },
  ];
  const allPresent = slots.every((s) => s.record != null);

  const [nickname, setNickname] = useState('');
  const [jurisdiction, setJurisdiction] = useState('USPA');
  const [lastRepackDate, setLastRepackDate] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const canContinue = allPresent && nickname.trim().length > 0;

  async function handleContinue() {
    if (submitting || !canContinue) return;
    setSubmitting(true);
    setError(null);
    try {
      const payload = {
        nickname: nickname.trim(),
        jurisdiction,
        current_container_id: created.container.id,
        current_main_id: created.main.id,
        current_reserve_id: created.reserve.id,
        current_aad_id: created.aad.id,
      };
      // D38 onboarding seed: when the user supplied a last-repack
      // date, write a single repack_history entry so the rig's
      // repack countdown starts ticking from that date. Mirrors
      // AddRigModal's "Onboarding entry" marker so the seed row is
      // discoverable as bootstrap data distinct from a real
      // rigger-recorded repack.
      if (lastRepackDate) {
        payload.repack_history = [
          {
            date: lastRepackDate,
            rigger: 'Onboarding entry',
            jurisdiction_seal: jurisdiction,
          },
        ];
      }
      const rig = await createRig(payload);
      onSubmit(rig);
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
          label="STEP 7 OF 7"
          title="Build your rig"
          blurb="Combine the four components you just added into a named rig. You can pick this rig on every jump from the log form."
        />

        {error && <ErrorBanner error={error} />}

        <Section label="COMPONENTS">
          <div className="rounded-lg p-3 mb-3" style={{
            background: 'var(--surface-2)',
            border: '0.5px solid var(--border)',
          }}>
            <ul className="space-y-1.5">
              {slots.map((slot) => {
                const ok = slot.record != null;
                return (
                  <li
                    key={slot.key}
                    className="flex items-start gap-2 text-[12px]"
                    style={{
                      color: ok
                        ? 'var(--text)'
                        : 'var(--text-muted)',
                    }}
                  >
                    {ok ? (
                      <CheckCircle2
                        className="w-3.5 h-3.5 mt-0.5 flex-shrink-0"
                        style={{ color: 'var(--status-ready)' }}
                      />
                    ) : (
                      <AlertCircle
                        className="w-3.5 h-3.5 mt-0.5 flex-shrink-0"
                        style={{ color: 'var(--status-watch)' }}
                      />
                    )}
                    <span className="flex-1 min-w-0">
                      <span className="font-medium">{slot.label}</span>
                      {ok ? (
                        <span className="ml-2 text-neutral-500">
                          {slot.record.manufacturer || '—'}
                          {slot.record.model ? ` ${slot.record.model}` : ''}
                          {slot.record.serial ? ` · SN ${slot.record.serial}` : ''}
                        </span>
                      ) : (
                        <span className="ml-2 text-neutral-500 italic">
                          skipped
                        </span>
                      )}
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        </Section>

        {!allPresent && <RigBlockedCard />}

        {allPresent && (
          <div className="space-y-4">
            <Field label="RIG NAME" required>
              <input
                type="text"
                value={nickname}
                onChange={(e) => setNickname(e.target.value)}
                className={inputCls}
                placeholder="e.g. Black Vector, Sabre rig, Sunday rig"
                maxLength={120}
              />
            </Field>

            <Field label="JURISDICTION" required>
              <div className="flex gap-1.5">
                {JURISDICTION_BUTTONS.map((j) => {
                  const active = jurisdiction === j.value;
                  return (
                    <button
                      key={j.value}
                      type="button"
                      onClick={() => setJurisdiction(j.value)}
                      className="flex-1 rounded-md px-3 py-2 text-[12px] font-medium transition"
                      style={{
                        background: active ? 'var(--text)' : 'var(--surface-2)',
                        color: active ? 'var(--bg)' : 'var(--text)',
                        border: '0.5px solid var(--border)',
                      }}
                    >
                      {j.label}
                    </button>
                  );
                })}
              </div>
              <div className="text-[10px] text-neutral-500 mt-1 leading-relaxed">
                Drives the next-repack-due window (USPA 180 days / CSPA 270 days; "both" picks the tighter).
              </div>
            </Field>

            <Field label="LAST REPACK DATE (used gear)">
              <input
                type="date"
                value={lastRepackDate}
                onChange={(e) => setLastRepackDate(e.target.value)}
                className={inputCls}
              />
              <div className="text-[10px] text-neutral-500 mt-1 leading-relaxed">
                Optional — leave blank for new gear. When set, seeds the
                repack clock so the next-due reminder reflects reality.
              </div>
            </Field>
          </div>
        )}
      </div>

      <StepFooter
        onBack={onBack}
        onSkip={onSkip}
        skipLabel={allPresent ? 'Skip — finish without a rig' : 'Continue without a rig'}
        onContinue={allPresent ? handleContinue : undefined}
        continueLabel="Build rig & finish"
        submitting={submitting}
        canContinue={canContinue}
      />
    </>
  );
}


function RigBlockedCard() {
  return (
    <div
      className="rounded-lg px-4 py-3 mb-3 text-[12px] leading-relaxed"
      style={{
        background: 'rgba(221,203,140,0.08)',
        border: '0.5px solid rgba(221,203,140,0.30)',
        color: '#ddcb8c',
      }}
    >
      <div className="font-medium mb-1">A rig needs all four components.</div>
      <div className="text-neutral-400">
        You skipped at least one of container / main / reserve / AAD,
        so we can't assemble a rig right now. That's okay — go to{' '}
        <span className="text-neutral-200">Inventory</span> to add the
        missing pieces, then to <span className="text-neutral-200">My Rig</span>{' '}
        to build the rig. The "Finish setup" banner on the Profile tab
        will keep nudging you until everything is in place.
      </div>
    </div>
  );
}
