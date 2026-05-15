import React, { useEffect, useState } from 'react';
import Sidebar from './Sidebar';
import Dashboard from './views/Dashboard';
import Jumps from './views/Jumps';
import MyRig from './views/MyRig';
import Inventory from './views/Inventory';
import Dropzones from './views/Dropzones';
import Settings from './views/Settings';
import OnboardingWizard from './views/OnboardingWizard';
import {
  ONBOARDING_RESUME_EVENT,
  ONBOARDING_STATE_CHANGED_EVENT,
} from './views/onboarding/ResumeBanner';
import { getOnboardingState } from './api';

const VIEWS = {
  dashboard: Dashboard,
  jumps: Jumps,
  myrig: MyRig,
  inventory: Inventory,
  dropzones: Dropzones,
  settings: Settings,
};

export default function App() {
  // App opens on the Dashboard — the new landing page hosts a
  // function bar (Log jump shortcut) and a configurable grid of
  // stats widgets. Identity moved into Settings.
  const [activeTab, setActiveTab] = useState('dashboard');
  const View = VIEWS[activeTab] || Dashboard;

  // D64: read the onboarding sentinel + the three "has_*" flags on
  // mount. Auto-show the wizard when the sentinel is absent and at
  // least one piece of foundation data is still missing. The
  // Profile resumption banner fires `ONBOARDING_RESUME_EVENT` to
  // re-open the wizard after dismissal — that flips
  // ``resumeOverride`` and shows the wizard regardless of the
  // sentinel state.
  //
  //   onboardingState:
  //     undefined → fetch in flight
  //     null      → fetch failed (treat as "no wizard" — better to
  //                 show the app than block on a transient backend
  //                 hiccup; the next reload will retry)
  //     object    → state from the backend
  const [onboardingState, setOnboardingState] = useState(undefined);
  const [resumeOverride, setResumeOverride] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getOnboardingState()
      .then((state) => { if (!cancelled) setOnboardingState(state); })
      .catch(() => { if (!cancelled) setOnboardingState(null); });
    return () => { cancelled = true; };
  }, []);

  // The resume banner dispatches a CustomEvent rather than calling
  // a prop because the banner lives inside Profile (a child of
  // <main>) and we want to avoid prop-drilling a setter through
  // every intermediate component. The handler is a single
  // ``setResumeOverride(true)``; the wizard, once mounted, posts
  // to the sentinel and unmounts via ``handleWizardDone``, which
  // clears the override back to false.
  useEffect(() => {
    // Guard the resume-event refetch against unmount: the user
    // could open and close the wizard quickly enough that the
    // in-flight ``getOnboardingState`` resolves after the
    // component has detached, which would trigger a setState on
    // an unmounted tree. Matches the cancelled-flag pattern on
    // the initial mount fetch above.
    let cancelled = false;
    function handleResume() {
      getOnboardingState()
        .then((state) => { if (!cancelled) setOnboardingState(state); })
        .catch(() => { /* keep previous state */ });
      setResumeOverride(true);
      // Make sure the user lands on Profile after dismissing — the
      // sidebar may have been on a different tab when they clicked
      // "Resume setup", and the wizard overlay covers it anyway.
      setActiveTab('profile');
    }
    window.addEventListener(ONBOARDING_RESUME_EVENT, handleResume);
    return () => {
      cancelled = true;
      window.removeEventListener(ONBOARDING_RESUME_EVENT, handleResume);
    };
  }, []);

  const autoShow = Boolean(
    onboardingState
    && !onboardingState.completed
    && (
      !onboardingState.has_jumper
      || !onboardingState.has_dropzones
      || !onboardingState.has_rigs
    ),
  );
  const showWizard = autoShow || (resumeOverride && Boolean(onboardingState));

  function handleWizardDone() {
    // After the sentinel write, mark the state completed locally
    // so the wizard unmounts on this render — no second backend
    // round-trip needed. The next App mount will see the fresh
    // sentinel and skip the wizard entirely.
    setOnboardingState((prev) => (prev ? { ...prev, completed: true } : prev));
    setResumeOverride(false);
    // Tell the ResumeBanner to refetch so it sees the updated
    // has_* flags (the user may have added their missing pieces
    // in this wizard run) and either hides or updates its copy.
    window.dispatchEvent(new CustomEvent(ONBOARDING_STATE_CHANGED_EVENT));
  }

  return (
    <div
      className="min-h-screen flex"
      style={{ background: 'var(--bg)', color: 'var(--text)' }}
    >
      <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} />
      <main className="flex-1 min-w-0 overflow-x-hidden">
        <View />
      </main>

      {showWizard && (
        <OnboardingWizard
          initialState={onboardingState}
          onDone={handleWizardDone}
        />
      )}
    </div>
  );
}
