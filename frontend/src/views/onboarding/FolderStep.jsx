import React, { useEffect, useState } from 'react';
import { FolderOpen, AlertTriangle, Loader2 } from 'lucide-react';
import { Section } from './formAtoms';
import { StepHeader, StepFooter } from './StepFrame';

// First wizard step (D65 update): pick where the logbook lives.
// Reuses the pywebview JsApi the Settings → LogbookSection already
// wires (``current_logbook_folder`` reads the path,
// ``change_logbook_folder`` pops the native picker + writes config,
// ``restart_app`` re-execs the process).
//
// Changing the folder requires a process restart — the backend is
// already bound to the previous folder and the in-memory wizard
// state is lost on restart. That's acceptable: the user just
// picked a fresh folder, the wizard re-opens auto on the new run.

function pywebviewApi() {
  if (typeof window !== 'undefined' && window.pywebview && window.pywebview.api) {
    return window.pywebview.api;
  }
  return null;
}

export default function FolderStep({ onContinue, onSkip }) {
  const [currentPath, setCurrentPath] = useState(null);
  const [pendingPath, setPendingPath] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const api = pywebviewApi();
    if (!api || typeof api.current_logbook_folder !== 'function') {
      setCurrentPath('(not available outside the desktop app)');
      return;
    }
    let cancelled = false;
    Promise.resolve(api.current_logbook_folder())
      .then((p) => { if (!cancelled) setCurrentPath(p); })
      .catch((err) => { if (!cancelled) setError(err); });
    return () => { cancelled = true; };
  }, []);

  async function handleChange() {
    const api = pywebviewApi();
    if (!api) {
      setError(new Error('Folder picker is only available in the desktop app.'));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.change_logbook_folder();
      if (result && result.path) {
        setPendingPath(result.path);
      }
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function handleRestart() {
    const api = pywebviewApi();
    if (!api) return;
    setBusy(true);
    try {
      await api.restart_app();
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <>
      <div className="px-7 py-7 flex-1 overflow-y-auto">
        <StepHeader
          label="STEP 1 OF 8"
          title="Welcome to your logbook."
          blurb="We'll get you ready to log jumps in a few quick steps. First — where should your logbook live on this Mac?"
        />

        {error && (
          <div
            className="flex items-start gap-2 rounded-lg px-3.5 py-2.5 mb-4 text-[12px]"
            style={{
              background: 'rgba(217,168,168,0.08)',
              border: '0.5px solid rgba(217,168,168,0.30)',
              color: '#d9a8a8',
            }}
          >
            <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
            <div>{error.message || String(error)}</div>
          </div>
        )}

        <Section label="LOGBOOK FOLDER">
          <div
            className="rounded-lg p-4"
            style={{
              background: 'var(--surface-2)',
              border: '0.5px solid var(--border)',
            }}
          >
            <div className="flex items-start gap-3">
              <FolderOpen
                className="w-4 h-4 mt-0.5 flex-shrink-0"
                style={{ color: 'var(--text-muted)' }}
              />
              <div className="flex-1 min-w-0">
                <div className="text-[10px] tracking-[0.25em] text-neutral-500 font-medium mb-1">
                  {pendingPath ? 'NEW LOCATION (NEEDS RESTART)' : 'CURRENT LOCATION'}
                </div>
                <div
                  className="text-[12px] font-mono break-all"
                  style={{ color: 'var(--text)' }}
                >
                  {pendingPath || currentPath || 'Loading…'}
                </div>
                <div className="text-[11px] text-neutral-500 mt-2 leading-relaxed">
                  Your jumps, gear, and dropzones will live in this folder as
                  plain XML files. You can move it later from Settings.
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2 mt-3">
              {pendingPath ? (
                <button
                  type="button"
                  onClick={handleRestart}
                  disabled={busy}
                  className="px-3 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition"
                  style={{
                    background: busy ? 'var(--surface-3)' : 'var(--text)',
                    color: busy ? 'var(--text-faint)' : 'var(--bg)',
                    cursor: busy ? 'not-allowed' : 'pointer',
                  }}
                >
                  {busy ? (
                    <>
                      <Loader2 className="w-3 h-3 animate-spin" />
                      Restarting…
                    </>
                  ) : (
                    'Restart with new folder'
                  )}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={handleChange}
                  disabled={busy}
                  className="px-3 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition"
                  style={{
                    background: 'var(--surface-1)',
                    color: 'var(--text)',
                    border: '0.5px solid var(--border)',
                    cursor: busy ? 'not-allowed' : 'pointer',
                  }}
                >
                  {busy ? (
                    <>
                      <Loader2 className="w-3 h-3 animate-spin" />
                      Picking…
                    </>
                  ) : (
                    'Choose a folder…'
                  )}
                </button>
              )}
            </div>
            {pendingPath && (
              <div className="text-[10px] text-neutral-500 mt-2 leading-relaxed">
                The new location is saved. Restart to switch the backend over
                — the wizard will pick up where you left off in the new
                folder.
              </div>
            )}
          </div>
        </Section>

        <div className="text-[11px] text-neutral-600 italic mt-5">
          Tip: avoid cloud-sync folders (Dropbox, iCloud, OneDrive) on the
          same logbook from more than one machine — they can race on
          writes. A local folder you back up separately is the safest
          option.
        </div>
      </div>

      <StepFooter
        onSkip={onSkip}
        skipLabel="Skip the rest"
        onContinue={onContinue}
        continueLabel="Get started"
      />
    </>
  );
}
