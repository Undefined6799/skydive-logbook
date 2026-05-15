import React, { useEffect, useState } from 'react';
import { Sparkles, ArrowRight, X } from 'lucide-react';
import { getOnboardingState } from '../../api';

// D64 resumption banner. Renders on the Profile tab when the
// onboarding sentinel is present (wizard was dismissed) but at
// least one of has_jumper / has_dropzones / has_rigs is still
// false. Clicking "Resume setup" fires a custom window event the
// App orchestrator listens for; App re-mounts the wizard.
//
// Why a window event rather than a context: the orchestrator
// already does the GET ``onboarding`` on App mount, so adding a
// context provider that exposes a setter would be a bigger
// refactor than the value here justifies. The event handler in
// App is one ``useEffect`` block.
//
// Dismissable per session via local state (no persistence) so a
// user who explicitly hides it now still gets re-nudged on next
// launch — matches the D64 "sentinel is final; banner is a nudge"
// split.
export const ONBOARDING_RESUME_EVENT = 'logbook:open-onboarding';

// Fired by App.jsx after the wizard dismisses so the banner
// refetches and either hides itself (everything done) or updates
// its "missing X and Y" copy. Without this the banner's local
// state stays frozen at the pre-resume snapshot for the rest of
// the session.
export const ONBOARDING_STATE_CHANGED_EVENT = 'logbook:onboarding-state-changed';


export default function ResumeBanner() {
  const [state, setState] = useState(undefined);
  const [hiddenThisSession, setHiddenThisSession] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getOnboardingState()
      .then((s) => { if (!cancelled) setState(s); })
      .catch(() => { if (!cancelled) setState(null); });
    return () => { cancelled = true; };
  }, [reloadKey]);

  // Refetch whenever the wizard closes — the user may have added
  // their missing pieces during the resume session, so the banner
  // either hides or updates its copy. Also reset the hide-for-
  // session flag: dismissing the wizard is a meaningful enough
  // action that the banner gets to show its new state once.
  useEffect(() => {
    function handleStateChange() {
      setHiddenThisSession(false);
      setReloadKey((k) => k + 1);
    }
    window.addEventListener(ONBOARDING_STATE_CHANGED_EVENT, handleStateChange);
    return () => {
      window.removeEventListener(ONBOARDING_STATE_CHANGED_EVENT, handleStateChange);
    };
  }, []);

  // The banner is conditional on:
  //   1. Sentinel present (`completed` true) — otherwise the
  //      auto-open wizard is handling onboarding, no banner needed.
  //   2. Some has_* flag still false — something to nudge about.
  //   3. User hasn't hidden the banner this session.
  if (!state) return null;
  if (!state.completed) return null;
  if (state.has_jumper && state.has_dropzones && state.has_rigs) return null;
  if (hiddenThisSession) return null;

  // Two banner-eligible flags today: has_dropzones, has_rigs. We
  // intentionally don't surface has_jumper here — Profile.jsx's
  // OnboardingForm owns jumper creation and is rendered above the
  // banner already (so the user is staring at the jumper form
  // while the banner shows). If a third flag is ever added, expand
  // the joiner below to handle the Oxford-comma case.
  const missing = [];
  if (!state.has_dropzones) missing.push('a home dropzone');
  if (!state.has_rigs) missing.push('a rig');
  const missingPhrase =
    missing.length === 1
      ? missing[0]
      : `${missing[0]} and ${missing[1]}`;

  function handleResume() {
    window.dispatchEvent(new CustomEvent(ONBOARDING_RESUME_EVENT));
  }

  return (
    <div
      className="rounded-xl px-5 py-4 mb-6 flex items-start gap-4"
      style={{
        background: 'var(--accent-soft)',
        border: '0.5px solid var(--accent)',
      }}
    >
      <div
        className="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0"
        style={{
          background: 'var(--surface-1)',
          border: '0.5px solid var(--border)',
        }}
      >
        <Sparkles className="w-4 h-4" style={{ color: 'var(--accent)' }} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-medium text-neutral-100 mb-1">
          Finish setup
        </div>
        <div className="text-[12px] text-neutral-400 leading-relaxed mb-3">
          You still need {missingPhrase} before the log form can
          pre-fill everything for you. Pick up where you left off — it'll
          skip what you already added.
        </div>
        <button
          type="button"
          onClick={handleResume}
          className="px-3.5 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition"
          style={{
            background: 'var(--text)',
            color: 'var(--bg)',
          }}
        >
          Resume setup
          <ArrowRight className="w-3 h-3" />
        </button>
      </div>
      <button
        type="button"
        onClick={() => setHiddenThisSession(true)}
        className="w-7 h-7 rounded-md flex items-center justify-center transition hover:bg-neutral-800 flex-shrink-0"
        aria-label="Hide for this session"
      >
        <X className="w-3 h-3 text-neutral-500" />
      </button>
    </div>
  );
}
