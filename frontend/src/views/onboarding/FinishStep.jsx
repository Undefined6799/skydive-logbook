import React from 'react';
import { CheckCircle2, BookOpen, AlertCircle } from 'lucide-react';
import { StepFooter } from './StepFrame';

// Terminal screen after the rig step (or after the user skipped the
// last form step). Renders a summary of what was created, the
// status (everything done vs. things still missing), and a single
// "Done" button that closes the wizard.
//
// The sentinel POST is owned by the orchestrator; this component
// is presentational. ``ready`` (from the orchestrator) tells us
// whether the logbook is set up — whether by this wizard run, by
// data already on file (resumption), or any mix. The summary list
// distinguishes the three sources per row: just-created, already
// on file, or skipped.
export default function FinishStep({ created, has, ready, onDone, dismissing }) {
  const allDone = ready;

  return (
    <>
      <div className="px-7 py-10 flex-1 overflow-y-auto flex flex-col items-center text-center">
        <div
          className="w-16 h-16 rounded-2xl flex items-center justify-center mb-5"
          style={{
            background: allDone
              ? 'rgba(168,213,181,0.10)'
              : 'rgba(221,203,140,0.10)',
            border: allDone
              ? '0.5px solid rgba(168,213,181,0.30)'
              : '0.5px solid rgba(221,203,140,0.30)',
          }}
        >
          {allDone ? (
            <CheckCircle2 className="w-7 h-7" style={{ color: 'var(--status-ready)' }} />
          ) : (
            <AlertCircle className="w-7 h-7" style={{ color: 'var(--status-watch)' }} />
          )}
        </div>

        <div className="text-[10px] tracking-[0.3em] text-neutral-500 font-medium mb-2">
          {allDone ? 'YOU’RE READY' : 'SETUP PAUSED'}
        </div>
        <div className="text-3xl font-medium tracking-tight mb-3 max-w-md">
          {allDone
            ? 'Time to log your first jump.'
            : 'Setup saved — you can finish anytime.'}
        </div>
        <div className="text-[13px] text-neutral-400 leading-relaxed max-w-md mb-8">
          {allDone
            ? 'You’ve got a dropzone, a full inventory, and a rig — the log form will pre-fill from your starred DZ and rig on every new jump.'
            : 'No rush. We saved what you entered. The "Finish setup" banner on the Profile tab points at the next missing piece whenever you want to come back.'}
        </div>

        <div
          className="rounded-lg px-4 py-3 text-left text-[12px] w-full max-w-md"
          style={{ background: 'var(--surface-2)', border: '0.5px solid var(--border)' }}
        >
          <div className="text-[10px] tracking-[0.25em] text-neutral-500 font-medium mb-2">
            WHAT YOU ADDED
          </div>
          <ul className="space-y-1">
            <SummaryRow
              label="Your profile"
              record={created.jumper}
              existed={has?.jumper}
            />
            <SummaryRow
              label="Home dropzone"
              record={created.dropzone}
              existed={has?.dropzones}
            />
            <SummaryRow
              label="Container"
              record={created.container}
              existed={has?.components}
            />
            <SummaryRow
              label="Main canopy"
              record={created.main}
              existed={has?.components}
            />
            <SummaryRow
              label="Reserve canopy"
              record={created.reserve}
              existed={has?.components}
            />
            <SummaryRow
              label="AAD"
              record={created.aad}
              existed={has?.components}
            />
            <SummaryRow
              label="Rig"
              record={created.rig}
              existed={has?.rigs}
            />
          </ul>
        </div>

        {allDone && (
          <div
            className="mt-6 flex items-center gap-2 text-[12px] text-neutral-500"
          >
            <BookOpen className="w-3.5 h-3.5" />
            <span>The Jumps tab is one click away from here.</span>
          </div>
        )}
      </div>

      <StepFooter
        onContinue={onDone}
        continueLabel={allDone ? 'Start logging jumps' : 'Done'}
        submitting={dismissing}
      />
    </>
  );
}


function SummaryRow({ label, record, existed }) {
  // Three possible row states:
  //   * present  — created in this wizard run; show the
  //                summary line.
  //   * existed  — was already on file (resumption path);
  //                show "on file".
  //   * skipped  — neither; the user skipped past with no
  //                existing record. Worth nudging about.
  const present = record != null;
  const state = present ? 'present' : existed ? 'existed' : 'skipped';
  const dotColor =
    state === 'skipped' ? 'var(--text-faint)' : 'var(--status-ready)';
  return (
    <li className="flex items-baseline gap-2">
      <span
        className="w-1.5 h-1.5 rounded-full flex-shrink-0 mt-1.5"
        style={{ background: dotColor }}
      />
      <span className="flex-1 min-w-0">
        <span className="text-neutral-300">{label}</span>
        {state === 'present' && (
          <span className="text-neutral-500 ml-2">
            {summariseRecord(record)}
          </span>
        )}
        {state === 'existed' && (
          <span className="text-neutral-500 ml-2 italic">on file</span>
        )}
        {state === 'skipped' && (
          <span className="text-neutral-600 ml-2 italic">skipped</span>
        )}
      </span>
    </li>
  );
}


function summariseRecord(record) {
  if (record.__kind === 'jumper') {
    const parts = [];
    if (record.name) parts.push(record.name);
    if (record.exit_weight_lb) {
      parts.push(`${record.exit_weight_lb} lb`);
    }
    return parts.join(' · ') || 'profile';
  }
  // ``__kind`` is stamped on by OnboardingWizard.recordCreated so
  // the summary branches on a discriminator instead of duck-typing
  // on which fields happen to be populated. Each branch picks the
  // most identifying field for that record kind, falling back to a
  // generic "added" if even that field is missing (e.g. a
  // container created with no manufacturer/model).
  if (record.__kind === 'dropzone') {
    return `${record.name} · ${record.city}`;
  }
  if (record.__kind === 'rig') {
    return record.nickname || 'rig';
  }
  // container / main / reserve / aad — identification fields are
  // optional, fall through manufacturer → model → serial → id stub.
  const parts = [];
  if (record.manufacturer) parts.push(record.manufacturer);
  if (record.model) parts.push(record.model);
  if (parts.length > 0) return parts.join(' ');
  if (record.serial) return `SN ${record.serial}`;
  if (record.id) return `id ${String(record.id).slice(0, 8)}…`;
  return 'added';
}
