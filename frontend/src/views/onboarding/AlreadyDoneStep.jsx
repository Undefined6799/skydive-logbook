import React from 'react';
import { CheckCircle2 } from 'lucide-react';
import { StepHeader, StepFooter } from './StepFrame';

// Shown in two situations:
//
//   1. **Pre-existing data** (resumption path). The user has the
//      entity on disk already — added outside the wizard, then
//      resumed via the Profile banner.
//   2. **Already saved this session** (Back-nav case). The user
//      filled the form, POSTed, then clicked Back. Re-rendering
//      the empty form and re-POSTing would create a duplicate
//      record, so we lock the step to a summary view instead.
//
// In both cases the contract is the same: a checkmark card plus a
// single Continue button. Back is still available if the user
// wants to revisit an earlier step; there is no Skip because the
// data exists. Edit lives on the regular tab after the wizard.
export default function AlreadyDoneStep({
  stepNumber,
  stepTitle,
  blurb,
  savedSummary,
  onContinue,
  onBack,
}) {
  // ``savedSummary``, if present, is rendered inside the green
  // card — used for the "session-just-saved" variant to show
  // exactly what the wizard captured. Without it the card is the
  // pre-existing-data variant which just confirms presence.
  const headerLabel = savedSummary ? `STEP ${stepNumber} OF 8` : `STEP ${stepNumber} OF 8`;
  return (
    <>
      <div className="px-7 py-7 flex-1 overflow-y-auto">
        <StepHeader
          label={headerLabel}
          title={stepTitle}
          blurb={blurb}
        />

        <div
          className="rounded-lg p-5 flex items-start gap-3"
          style={{
            background: 'rgba(168,213,181,0.08)',
            border: '0.5px solid rgba(168,213,181,0.30)',
          }}
        >
          <CheckCircle2
            className="w-5 h-5 mt-0.5 flex-shrink-0"
            style={{ color: 'var(--status-ready)' }}
          />
          <div className="flex-1 min-w-0">
            <div
              className="text-[13px] font-medium mb-1"
              style={{ color: 'var(--status-ready)' }}
            >
              {savedSummary ? 'Saved' : 'Already added'}
            </div>
            {savedSummary && (
              <div className="text-[12px] text-neutral-200 leading-relaxed mb-1.5">
                {savedSummary}
              </div>
            )}
            <div className="text-[12px] text-neutral-400 leading-relaxed">
              {savedSummary
                ? "We won't re-save this if you continue. Edit it from the regular tab after setup if you need to change anything."
                : "You've already added this from outside the wizard. Edit it any time from the regular tab."}
            </div>
          </div>
        </div>
      </div>

      <StepFooter
        onBack={onBack}
        onContinue={onContinue}
        continueLabel="Continue"
      />
    </>
  );
}
