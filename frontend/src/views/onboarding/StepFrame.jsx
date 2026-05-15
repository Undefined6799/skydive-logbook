import React from 'react';
import { Loader2, ArrowRight, ArrowLeft } from 'lucide-react';

// Header + footer chrome each step renders so the wizard
// orchestrator stays focused on routing. Every form step pairs
// StepHeader at the top of its body and StepFooter at the bottom;
// the Welcome / Finish screens use only the footer.

export function StepHeader({ label, title, blurb }) {
  return (
    <div className="mb-5">
      {label && (
        <div className="text-[10px] tracking-[0.3em] text-neutral-500 font-medium mb-2">
          {label}
        </div>
      )}
      <div className="text-2xl font-medium tracking-tight mb-1">{title}</div>
      {blurb && (
        <div className="text-[12px] text-neutral-500 leading-relaxed">{blurb}</div>
      )}
    </div>
  );
}


// Renders Back / Skip / Continue. Each callback is optional —
// omitting `onBack` hides the Back button (Welcome step), omitting
// `onSkip` hides Skip (Finish step). `continueLabel` overrides the
// default "Continue".
export function StepFooter({
  onBack,
  onSkip,
  onContinue,
  continueLabel = 'Continue',
  skipLabel = 'Skip this step',
  submitting = false,
  canContinue = true,
}) {
  return (
    <div
      className="flex items-center justify-between px-7 py-4 mt-auto"
      style={{ borderTop: '0.5px solid var(--border-strong)' }}
    >
      <div>
        {onSkip && (
          <button
            type="button"
            onClick={onSkip}
            disabled={submitting}
            className="text-[12px] text-neutral-500 hover:text-neutral-300 transition disabled:opacity-50"
          >
            {skipLabel}
          </button>
        )}
      </div>

      <div className="flex items-center gap-2">
        {onBack && (
          <button
            type="button"
            onClick={onBack}
            disabled={submitting}
            className="px-3 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition disabled:opacity-50"
            style={{
              background: 'var(--surface-2)',
              color: 'var(--text)',
              border: '0.5px solid var(--border)',
              cursor: submitting ? 'not-allowed' : 'pointer',
            }}
          >
            <ArrowLeft className="w-3 h-3" />
            Back
          </button>
        )}
        {onContinue && (
          <button
            type="button"
            onClick={onContinue}
            disabled={submitting || !canContinue}
            className="px-4 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition"
            style={{
              background: submitting || !canContinue ? 'var(--surface-3)' : 'var(--text)',
              color: submitting || !canContinue ? 'var(--text-faint)' : 'var(--bg)',
              cursor: submitting || !canContinue ? 'not-allowed' : 'pointer',
            }}
          >
            {submitting ? (
              <>
                <Loader2 className="w-3 h-3 animate-spin" />
                Saving…
              </>
            ) : (
              <>
                {continueLabel}
                <ArrowRight className="w-3 h-3" />
              </>
            )}
          </button>
        )}
      </div>
    </div>
  );
}
