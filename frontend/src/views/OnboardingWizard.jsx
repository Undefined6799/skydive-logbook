import React, { useState } from 'react';
import { completeOnboarding } from '../api';
import FolderStep from './onboarding/FolderStep';
import JumperStep from './onboarding/JumperStep';
import DropzoneStep from './onboarding/DropzoneStep';
import ContainerStep from './onboarding/ContainerStep';
import MainStep from './onboarding/MainStep';
import ReserveStep from './onboarding/ReserveStep';
import AadStep from './onboarding/AadStep';
import RigStep from './onboarding/RigStep';
import FinishStep from './onboarding/FinishStep';
import AlreadyDoneStep from './onboarding/AlreadyDoneStep';

// D64 first-run wizard. Orchestrates 8 numbered steps + a terminal
// finish screen:
//
//   folder → jumper → dropzone → container → main → reserve → aad → rig → finish
//
// Each form step POSTs through the existing entity endpoints. The
// Folder step uses the pywebview JsApi (not REST) because the
// logbook root is per-process config, not part of the on-disk
// record set. This module owns the routing + the sentinel POST.
//
// Two open paths:
//
//   * **First run** — sentinel absent + something missing.
//     App.jsx auto-opens; the user walks linearly forward.
//   * **Resumption** — sentinel present + some has_* still false,
//     OR Settings "Re-run setup wizard" pressed. Profile banner
//     and Settings button both fire ``logbook:open-onboarding``;
//     App.jsx re-opens this wizard. Steps whose ``has_*`` flag
//     is already true render :class:`AlreadyDoneStep` so the
//     user lands at the missing piece quickly.

const STEP_KEYS = [
  'folder',
  'jumper',
  'dropzone',
  'container',
  'main',
  'reserve',
  'aad',
  'rig',
  'finish',
];


export default function OnboardingWizard({ initialState, onDone }) {
  const [currentStep, setCurrentStep] = useState('folder');
  const [created, setCreated] = useState({
    jumper: null,
    dropzone: null,
    container: null,
    main: null,
    reserve: null,
    aad: null,
    rig: null,
  });
  const [dismissing, setDismissing] = useState(false);
  const [error, setError] = useState(null);

  // Lock body scroll while the wizard is open. Mirror DropzoneModal
  // / AddComponentModal so the wizard feels consistent with the
  // existing modal stack.
  React.useEffect(() => {
    if (document?.body?.style) {
      document.body.style.overflow = 'hidden';
    }
    return () => {
      if (document?.body?.style) {
        document.body.style.overflow = '';
      }
    };
  }, []);

  // ``components`` is derived from ``has_rigs``: D37 enforces that
  // a rig references one container + main + reserve + AAD, all
  // active at create time. So when ``has_rigs=true`` we know all
  // four component kinds are populated somewhere in inventory,
  // and the wizard's per-kind steps can render AlreadyDoneStep
  // instead of empty forms.
  const has = {
    jumper: Boolean(initialState?.has_jumper),
    dropzones: Boolean(initialState?.has_dropzones),
    rigs: Boolean(initialState?.has_rigs),
    components: Boolean(initialState?.has_rigs),
  };

  function advance() {
    const idx = STEP_KEYS.indexOf(currentStep);
    if (idx < 0 || idx >= STEP_KEYS.length - 1) {
      setCurrentStep('finish');
      return;
    }
    setCurrentStep(STEP_KEYS[idx + 1]);
  }

  function goBack() {
    const idx = STEP_KEYS.indexOf(currentStep);
    if (idx <= 0) return;
    setCurrentStep(STEP_KEYS[idx - 1]);
  }

  function recordCreated(key, record) {
    // Stamp the wizard step key onto the record before storing so
    // FinishStep's summary can branch on ``__kind`` rather than
    // duck-typing on which fields happen to be populated.
    setCreated((prev) => ({ ...prev, [key]: { ...record, __kind: key } }));
    advance();
  }

  function skipStep() {
    advance();
  }

  async function dismissWizard(status) {
    if (dismissing) return;
    setDismissing(true);
    setError(null);
    try {
      await completeOnboarding(status);
      onDone();
    } catch (err) {
      setError(err);
      setDismissing(false);
    }
  }

  function renderStep() {
    if (currentStep === 'folder') {
      return (
        <FolderStep
          onContinue={advance}
          onSkip={() => dismissWizard('skipped')}
        />
      );
    }

    // Each step-handler below renders one of three views, in
    // order of precedence:
    //
    //   1. **Saved this session** — ``created[stepKey]`` is set,
    //      meaning the user already filled and POSTed this step
    //      during the current wizard run. Re-rendering the form
    //      would lose state and re-POST on Continue → duplicate
    //      record. AlreadyDoneStep with a ``savedSummary`` locks
    //      the step to a read-only view.
    //   2. **Pre-existing data** — ``has[X]`` is true but nothing
    //      was created this session (resumption path).
    //      AlreadyDoneStep with no ``savedSummary``.
    //   3. **Fresh form** — neither of the above; render the form.

    if (currentStep === 'jumper') {
      if (created.jumper) {
        return (
          <AlreadyDoneStep
            stepNumber={2}
            stepTitle="Your profile"
            blurb="The profile you just saved — keep going."
            savedSummary={summariseJumper(created.jumper)}
            onContinue={advance}
            onBack={goBack}
          />
        );
      }
      if (has.jumper) {
        return (
          <AlreadyDoneStep
            stepNumber={2}
            stepTitle="Your profile"
            blurb="You already have a jumper profile on this logbook. Edit it from the Profile tab."
            onContinue={advance}
            onBack={goBack}
          />
        );
      }
      return (
        <JumperStep
          onSubmit={(rec) => recordCreated('jumper', rec)}
          onSkip={skipStep}
          onBack={goBack}
        />
      );
    }

    if (currentStep === 'dropzone') {
      if (created.dropzone) {
        return (
          <AlreadyDoneStep
            stepNumber={3}
            stepTitle="Your home dropzone"
            blurb="The dropzone you just saved — keep going."
            savedSummary={summariseDropzone(created.dropzone)}
            onContinue={advance}
            onBack={goBack}
          />
        );
      }
      if (has.dropzones) {
        return (
          <AlreadyDoneStep
            stepNumber={3}
            stepTitle="Your home dropzone"
            blurb="You already added a dropzone from the Dropzones tab. Use the regular tab to add more or edit details."
            onContinue={advance}
            onBack={goBack}
          />
        );
      }
      return (
        <DropzoneStep
          onSubmit={(rec) => recordCreated('dropzone', rec)}
          onSkip={skipStep}
          onBack={goBack}
        />
      );
    }

    if (currentStep === 'container') {
      if (created.container) {
        return (
          <AlreadyDoneStep
            stepNumber={4}
            stepTitle="Your container"
            blurb="The container you just saved — keep going."
            savedSummary={summariseComponent(created.container)}
            onContinue={advance}
            onBack={goBack}
          />
        );
      }
      if (has.components) {
        return (
          <AlreadyDoneStep
            stepNumber={4}
            stepTitle="Your container"
            blurb="You already have inventory components on this logbook — found via your existing rig. Edit any of them from the Inventory tab."
            onContinue={advance}
            onBack={goBack}
          />
        );
      }
      return (
        <ContainerStep
          onSubmit={(rec) => recordCreated('container', rec)}
          onSkip={skipStep}
          onBack={goBack}
        />
      );
    }

    if (currentStep === 'main') {
      if (created.main) {
        return (
          <AlreadyDoneStep
            stepNumber={5}
            stepTitle="Your main canopy"
            blurb="The main canopy you just saved — keep going."
            savedSummary={summariseComponent(created.main)}
            onContinue={advance}
            onBack={goBack}
          />
        );
      }
      if (has.components) {
        return (
          <AlreadyDoneStep
            stepNumber={5}
            stepTitle="Your main canopy"
            blurb="Already on file from your existing rig. Lineset and wear history live on the Inventory tab."
            onContinue={advance}
            onBack={goBack}
          />
        );
      }
      return (
        <MainStep
          onSubmit={(rec) => recordCreated('main', rec)}
          onSkip={skipStep}
          onBack={goBack}
        />
      );
    }

    if (currentStep === 'reserve') {
      if (created.reserve) {
        return (
          <AlreadyDoneStep
            stepNumber={6}
            stepTitle="Your reserve canopy"
            blurb="The reserve canopy you just saved — keep going."
            savedSummary={summariseComponent(created.reserve)}
            onContinue={advance}
            onBack={goBack}
          />
        );
      }
      if (has.components) {
        return (
          <AlreadyDoneStep
            stepNumber={6}
            stepTitle="Your reserve canopy"
            blurb="Already on file from your existing rig. Repack history and currency live on the Inventory tab."
            onContinue={advance}
            onBack={goBack}
          />
        );
      }
      return (
        <ReserveStep
          onSubmit={(rec) => recordCreated('reserve', rec)}
          onSkip={skipStep}
          onBack={goBack}
        />
      );
    }

    if (currentStep === 'aad') {
      if (created.aad) {
        return (
          <AlreadyDoneStep
            stepNumber={7}
            stepTitle="Your AAD"
            blurb="The AAD you just saved — keep going."
            savedSummary={summariseComponent(created.aad)}
            onContinue={advance}
            onBack={goBack}
          />
        );
      }
      if (has.components) {
        return (
          <AlreadyDoneStep
            stepNumber={7}
            stepTitle="Your AAD"
            blurb="Already on file from your existing rig. Recertification calendar lives on the Inventory tab."
            onContinue={advance}
            onBack={goBack}
          />
        );
      }
      return (
        <AadStep
          onSubmit={(rec) => recordCreated('aad', rec)}
          onSkip={skipStep}
          onBack={goBack}
        />
      );
    }

    if (currentStep === 'rig') {
      if (created.rig) {
        return (
          <AlreadyDoneStep
            stepNumber={8}
            stepTitle="Your rig"
            blurb="The rig you just saved — keep going."
            savedSummary={created.rig.nickname || 'rig'}
            onContinue={advance}
            onBack={goBack}
          />
        );
      }
      if (has.rigs) {
        return (
          <AlreadyDoneStep
            stepNumber={8}
            stepTitle="Your rig"
            blurb="You already have a rig in My Rig. The wizard's job is done — keep building from the regular tab."
            onContinue={advance}
            onBack={goBack}
          />
        );
      }
      return (
        <RigStep
          created={created}
          onSubmit={(rec) => recordCreated('rig', rec)}
          onSkip={skipStep}
          onBack={goBack}
        />
      );
    }

    // currentStep === 'finish'.
    // "Ready" = every piece either was created in this run OR was
    // already on file (resumption path). Jumper and dropzone have
    // their own has_* flags; the four component kinds derive from
    // has.rigs (D37 guarantees a rig implies all four components).
    const ready = Boolean(
      (created.jumper || has.jumper)
      && (created.dropzone || has.dropzones)
      && (created.rig || has.rigs),
    );
    // Sentinel ``status`` reflects whether the user actively
    // walked every form. AlreadyDoneStep clicks don't count as
    // "finished" because the user didn't input anything.
    const everyFormSubmitted = Boolean(
      created.jumper && created.dropzone && created.container && created.main
      && created.reserve && created.aad && created.rig,
    );
    const sentinelStatus = everyFormSubmitted ? 'finished' : 'skipped';
    return (
      <FinishStep
        created={created}
        has={has}
        ready={ready}
        dismissing={dismissing}
        onDone={() => dismissWizard(sentinelStatus)}
      />
    );
  }

  const stepIndex = Math.max(0, STEP_KEYS.indexOf(currentStep));

  return (
    <>
      <div
        className="fixed inset-0 z-40"
        style={{
          background: 'rgba(0,0,0,0.85)',
          backdropFilter: 'blur(6px)',
        }}
      />
      <div className="fixed inset-0 z-50 flex items-start justify-center p-6 pointer-events-none overflow-y-auto">
        <div
          className="rounded-2xl w-full max-w-2xl pointer-events-auto mt-10 mb-10 flex flex-col"
          style={{
            background: 'var(--surface-1)',
            border: '0.5px solid var(--border-strong)',
            maxHeight: 'calc(100vh - 80px)',
          }}
        >
          <ProgressBar steps={STEP_KEYS} currentIndex={stepIndex} />

          {error && (
            <div
              className="px-7 py-3 text-[12px]"
              style={{
                background: 'rgba(217,168,168,0.08)',
                color: '#d9a8a8',
                borderBottom: '0.5px solid var(--border-strong)',
              }}
            >
              Could not save: {error?.message || String(error)}
            </div>
          )}

          {renderStep()}
        </div>
      </div>
    </>
  );
}


// Short one-liners used to confirm "this is what we saved" on the
// Back-nav lock card. Each branches on the wizard's known record
// kind — the wizard owns this knowledge so per-step components
// don't need a "render-as-summary" mode.

function summariseJumper(j) {
  const parts = [];
  if (j.name) parts.push(j.name);
  if (j.exit_weight_lb) parts.push(`${j.exit_weight_lb} lb`);
  return parts.join(' · ') || 'profile saved';
}

function summariseDropzone(d) {
  return [d.name, d.city].filter(Boolean).join(' · ');
}

function summariseComponent(c) {
  const parts = [];
  if (c.manufacturer) parts.push(c.manufacturer);
  if (c.model) parts.push(c.model);
  if (parts.length > 0) return parts.join(' ');
  if (c.serial) return `SN ${c.serial}`;
  return 'saved';
}


function ProgressBar({ steps, currentIndex }) {
  // Steps minus the trailing "finish" entry — the dots represent
  // user-facing pages with a number ("STEP N OF 8"); the finish
  // screen is the terminal celebration / summary and isn't part
  // of the numbered sequence.
  const visibleSteps = steps.slice(0, -1);
  const adjustedIndex = Math.min(currentIndex, visibleSteps.length - 1);

  return (
    <div
      className="flex items-center gap-1.5 px-7 py-4"
      style={{ borderBottom: '0.5px solid var(--border-strong)' }}
    >
      {visibleSteps.map((step, idx) => {
        const active = idx === adjustedIndex;
        const passed = idx < adjustedIndex || currentIndex === steps.length - 1;
        return (
          <div
            key={step}
            className="flex-1 h-1 rounded-full transition-colors"
            style={{
              background:
                active || passed ? 'var(--text)' : 'var(--surface-3)',
              opacity: active ? 1 : passed ? 0.6 : 1,
            }}
            aria-label={`Step ${idx + 1}: ${step}`}
          />
        );
      })}
      <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium pl-3 whitespace-nowrap">
        {currentIndex === steps.length - 1
          ? 'DONE'
          : `${String(adjustedIndex + 1).padStart(2, '0')} / ${String(visibleSteps.length).padStart(2, '0')}`}
      </div>
    </div>
  );
}
