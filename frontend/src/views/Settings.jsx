import React, { useState, useEffect } from 'react';
import {
  RefreshCw,
  Check,
  Loader2,
  AlertTriangle,
  Download,
  ExternalLink,
  Sparkles,
} from 'lucide-react';
import { runVerify, runReindex, checkForUpdates, ApiError } from '../api';
import { StatusDot, GhostButton, Card, SectionLabel } from '../primitives';
import { useAltitudeUnit } from '../units';
import IdentityManager from './Identity';
import { ONBOARDING_RESUME_EVENT } from './onboarding/ResumeBanner';

// Bridge to the pywebview JS API. Returns null when running in the
// browser (Vite dev mode) where pywebview is absent — callers fall
// back to a friendly message.
function pywebviewApi() {
  if (typeof window !== 'undefined' && window.pywebview && window.pywebview.api) {
    return window.pywebview.api;
  }
  return null;
}

export default function Settings() {
  return (
    <div className="px-10 py-10 max-w-[860px]">
      <div className="mb-5">
        <div className="text-3xl font-medium tracking-tight">Settings</div>
        <div className="text-[12px] text-neutral-500 mt-1.5">Logbook configuration and preferences.</div>
      </div>

      <IdentitySection />
      <LogbookSection />
      <UnitsSection />
      <OnboardingSection />
      <VerifySection />
      <TrashSection />
      <DiagnosticsSection />
      <UpdatesSection />
      <AboutSection />
    </div>
  );
}

// D65: surface the first-run wizard from Settings so a user with
// an existing logbook can step through it (the auto-open path
// targets empty logbooks only). Clicking the button fires the
// same window event the Dashboard resume banner dispatches;
// App.jsx flips ``resumeOverride`` and the wizard mounts. The
// user can either walk forward (steps where data already exists
// render AlreadyDoneStep) or skip out — either way the sentinel
// gets stamped on dismiss.
function OnboardingSection() {
  function handleRunWizard() {
    window.dispatchEvent(new CustomEvent(ONBOARDING_RESUME_EVENT));
  }
  return (
    <Card className="p-5 mb-2.5">
      <SectionLabel>FIRST-RUN WIZARD</SectionLabel>
      <div className="text-[12px] text-neutral-400 mb-3 leading-relaxed">
        Walk through the guided setup again. Useful for revisiting
        what's there, or running the wizard end-to-end on a logbook
        that was set up before this feature shipped.
      </div>
      <GhostButton onClick={handleRunWizard}>
        <Sparkles className="w-3.5 h-3.5" />
        Re-run setup wizard
      </GhostButton>
    </Card>
  );
}

// Identity moved into Settings from the (now-removed) Profile tab.
// IdentityManager renders its own card chrome (matching the rest of
// the identity edit flow), so we don't wrap it in a Settings Card —
// the SectionLabel sits above the card the same way the legacy
// Profile page laid them out.
function IdentitySection() {
  return (
    <div className="mb-2.5">
      <div className="px-1 mb-2">
        <SectionLabel>IDENTITY</SectionLabel>
      </div>
      <IdentityManager />
    </div>
  );
}

function LogbookSection() {
  // Pull the current path from the launcher's JS API. In the browser
  // (Vite dev) the API isn't present, so we render a placeholder.
  const [currentPath, setCurrentPath] = useState(null);
  const [pendingPath, setPendingPath] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const api = pywebviewApi();
    if (!api) {
      setCurrentPath(null);
      return;
    }
    let cancelled = false;
    Promise.resolve(api.current_logbook_folder())
      .then((p) => { if (!cancelled) setCurrentPath(p); })
      .catch(() => { if (!cancelled) setCurrentPath(null); });
    return () => { cancelled = true; };
  }, []);

  async function handleChange() {
    const api = pywebviewApi();
    if (!api) {
      setError('Folder picker is only available in the desktop app.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.change_logbook_folder();
      if (result && result.path) {
        setPendingPath(result.path);
      }
    } catch (e) {
      setError(e?.message || 'Folder picker failed.');
    } finally {
      setBusy(false);
    }
  }

  async function handleRestart() {
    const api = pywebviewApi();
    if (!api) return;
    await api.restart_app();
  }

  const displayPath = pendingPath || currentPath || '~/SkydiveLogbook';

  return (
    <Card className="p-5 mb-2.5">
      <SectionLabel>LOGBOOK FOLDER</SectionLabel>
      <div className="flex items-center gap-2.5 mb-3">
        <div
          className="flex-1 min-w-0 rounded-lg px-3 py-2 font-mono text-[12px] text-neutral-300 truncate"
          style={{ background: 'var(--bg)', border: '0.5px solid var(--border-strong)' }}
          title={displayPath}
        >
          {displayPath}
        </div>
        <GhostButton onClick={handleChange} disabled={busy}>
          {busy ? 'Choosing…' : 'Change…'}
        </GhostButton>
        <GhostButton
          onClick={() => {
            const api = pywebviewApi();
            if (api) api.reveal_logbook_root();
          }}
        >
          Reveal
        </GhostButton>
      </div>
      {pendingPath ? (
        <div
          className="rounded-lg p-3 mb-3 flex items-start gap-2.5"
          style={{ background: 'var(--status-watch-bg)', border: '0.5px solid var(--status-watch)' }}
        >
          <Check className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: 'var(--status-watch)' }} />
          <div className="flex-1">
            <div className="text-[12px] text-neutral-100">
              New logbook saved. Restart to load it.
            </div>
            <div className="text-[11px] text-neutral-500 mt-0.5 font-mono truncate">{pendingPath}</div>
          </div>
          <button
            onClick={handleRestart}
            className="px-2.5 py-1 text-[11px] font-medium rounded-md transition flex items-center gap-1"
            style={{ background: 'var(--status-watch)', color: 'var(--bg)' }}
          >
            <RefreshCw className="w-3 h-3" />
            Restart now
          </button>
        </div>
      ) : null}
      {error && (
        <div className="text-[11px] text-neutral-400 mb-2">{error}</div>
      )}
      <div className="text-[11px] text-neutral-500 leading-relaxed">
        Holds <span className="font-mono text-neutral-400">SCHEMA.v1.xsd</span>,{' '}
        <span className="font-mono text-neutral-400">MANIFEST.json</span>,{' '}
        <span className="font-mono text-neutral-400">jumps/</span>,{' '}
        <span className="font-mono text-neutral-400">rigs/</span>,{' '}
        <span className="font-mono text-neutral-400">inventory/</span>, and{' '}
        <span className="font-mono text-neutral-400">index.sqlite</span>.
      </div>
    </Card>
  );
}

function UnitsSection() {
  const [altitudeUnit, setAltitudeUnit] = useAltitudeUnit();
  return (
    <Card className="p-5 mb-2.5">
      <SectionLabel>UNITS</SectionLabel>
      <div className="grid grid-cols-[100px_1fr] gap-3 items-center">
        <span className="text-[12px] text-neutral-400">Altitude</span>
        <Segmented
          value={altitudeUnit}
          options={['m', 'ft']}
          onChange={setAltitudeUnit}
        />
        {/* Speed and weight units are reserved seams — the Settings
            UI surfaces the choice for forward-compatibility, but
            v0.1 doesn't have any speed or weight inputs to convert.
            They light up once D33 rig manager fields land. */}
        <span className="text-[12px] text-neutral-400">Speed</span>
        <Segmented value="km/h" options={['km/h', 'mph']} disabled />
        <span className="text-[12px] text-neutral-400">Weight</span>
        <Segmented value="lb" options={['kg', 'lb']} disabled />
      </div>
      <div className="text-[10px] text-neutral-500 mt-3">
        Storage stays in canonical SI units (meters, m/s, kg). Switching only
        changes how values are displayed and entered — no data is rewritten.
      </div>
    </Card>
  );
}

function VerifySection() {
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(null); // { folders_scanned, clean, issues: [...] }
  const [error, setError] = useState(null);
  const [reindexBusy, setReindexBusy] = useState(false);
  const [reindexReport, setReindexReport] = useState(null);

  async function handleVerify() {
    setBusy(true);
    setError(null);
    try {
      const r = await runVerify();
      setReport(r);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function handleReindex() {
    setReindexBusy(true);
    setError(null);
    try {
      const r = await runReindex();
      setReindexReport(r);
    } catch (err) {
      setError(err);
    } finally {
      setReindexBusy(false);
    }
  }

  return (
    <Card className="p-5 mb-2.5">
      <div className="flex items-center justify-between mb-3">
        <SectionLabel>VERIFY &amp; REINDEX</SectionLabel>
        {report && (
          <div className="flex items-center gap-1.5">
            <StatusDot status={report.clean ? 'green' : 'yellow'} />
            <span
              className="text-[11px] font-medium"
              style={{ color: report.clean ? 'var(--status-ready)' : 'var(--status-watch)' }}
            >
              {report.clean
                ? `Clean · ${report.folders_scanned} folders scanned`
                : `${report.issues.length} issue${report.issues.length === 1 ? '' : 's'} found`}
            </span>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2.5 flex-wrap">
        <button
          onClick={handleVerify}
          disabled={busy}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-[12px] rounded-lg transition disabled:opacity-50"
          style={{ background: 'transparent', color: 'var(--text)', border: '0.5px solid var(--border-strong)' }}
        >
          {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
          {busy ? 'Verifying…' : 'Run integrity check'}
        </button>
        <button
          onClick={handleReindex}
          disabled={reindexBusy}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-[12px] rounded-lg transition disabled:opacity-50"
          style={{ background: 'transparent', color: 'var(--text)', border: '0.5px solid var(--border-strong)' }}
        >
          {reindexBusy ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
          {reindexBusy ? 'Reindexing…' : 'Reindex from XML'}
        </button>
        <span className="text-[11px] text-neutral-500">
          Verify checks every <span className="font-mono">jump.xml</span> against the schema and rehashes
          attachments. Reindex rebuilds the SQLite index from disk.
        </span>
      </div>

      {report && !report.clean && (
        <div className="mt-3 p-3 rounded-lg" style={{ background: 'var(--bg)', border: '0.5px solid var(--border-strong)' }}>
          <div className="text-[10px] tracking-[0.2em] text-neutral-500 font-medium mb-1.5">
            ISSUES
          </div>
          <div className="space-y-1.5">
            {report.issues.map((issue, i) => (
              <div key={i} className="text-[11px] font-mono">
                <span style={{ color: 'var(--status-watch)' }}>{issue.kind}</span>
                <span className="text-neutral-500"> · </span>
                <span className="text-neutral-300">{issue.folder}</span>
                <div className="text-neutral-500 ml-2">{issue.detail}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {reindexReport && (
        <div className="mt-3 p-3 rounded-lg text-[11px]"
             style={{
               background: 'var(--bg)',
               border: `0.5px solid ${reindexReport.clean ? 'var(--border-strong)' : 'var(--status-watch)'}`,
             }}>
          <div className="font-mono text-neutral-300">
            {reindexReport.aborted
              ? <span style={{ color: 'var(--status-critical)' }}>ABORTED: {reindexReport.aborted}</span>
              : <>
                  <span style={{ color: reindexReport.clean ? 'var(--status-ready)' : 'var(--status-watch)' }}>
                    {reindexReport.jumps_indexed} jumps indexed
                  </span>
                  {' · '}
                  <span className="text-neutral-500">{reindexReport.folders_scanned} folders scanned</span>
                  {reindexReport.skipped.length > 0 && (
                    <>
                      {' · '}<span style={{ color: 'var(--status-watch)' }}>{reindexReport.skipped.length} skipped</span>
                    </>
                  )}
                </>}
          </div>
          {reindexReport.skipped.length > 0 && (
            <div className="mt-1.5 space-y-0.5">
              {reindexReport.skipped.map(([folder, reason], i) => (
                <div key={i} className="text-neutral-500 font-mono">
                  <span className="text-neutral-300">{folder}</span> — {reason}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {error && (
        <div
          className="mt-3 p-3 rounded-lg flex items-start gap-2"
          style={{ background: 'rgba(248,113,113,0.05)', border: '0.5px solid rgba(248,113,113,0.25)' }}
        >
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" style={{ color: 'var(--status-critical)' }} />
          <div className="text-[12px] text-neutral-300">
            {error instanceof ApiError
              ? (error.problem?.detail || error.message)
              : error.message}
          </div>
        </div>
      )}
    </Card>
  );
}

// Trash listing + restore is slated for v0.2. The backend currently
// has no GET /api/v1/trash or restore endpoint; soft_delete writes
// to <logbook_root>/.trash/ (D19) but no service surfaces it. Until
// then this section renders a placeholder so the UI doesn't claim
// data it can't produce.
function TrashSection() {
  return (
    <Card className="p-4 px-5 mb-2.5">
      <SectionLabel>TRASH</SectionLabel>
      <div className="text-[12px] text-neutral-400 leading-relaxed">
        Deleted jumps and retired components are kept in{' '}
        <span className="font-mono text-neutral-300">
          &lt;logbook&gt;/.trash/
        </span>{' '}
        on disk (D19). An in-app listing and restore flow is planned
        for v0.2.
      </div>
    </Card>
  );
}

function DiagnosticsSection() {
  // The reveal-* buttons go through the pywebview JS API to open
  // Finder / Explorer at the right place. Outside the desktop app
  // (Vite dev mode) the API isn't injected — surface a friendly
  // message instead of silently doing nothing. ``error`` carries
  // either the JS API's own ``{ok:false, error}`` payload (e.g.
  // "logs folder does not exist") or the dev-mode fallback message.
  const [error, setError] = useState(null);

  async function reveal(kind) {
    setError(null);
    const api = pywebviewApi();
    if (!api) {
      setError(`${kind === 'logs' ? 'Logs folder' : 'Config file'} can only be opened from the desktop app.`);
      return;
    }
    const fn = kind === 'logs' ? api.reveal_logs_folder : api.reveal_config_file;
    if (typeof fn !== 'function') {
      setError(`This build of the desktop app doesn't expose ${kind === 'logs' ? 'reveal_logs_folder' : 'reveal_config_file'} yet.`);
      return;
    }
    try {
      const result = await fn();
      if (result && result.ok === false) {
        setError(result.error || 'Could not reveal the folder.');
      }
    } catch (e) {
      setError(e?.message || 'Could not reveal the folder.');
    }
  }

  return (
    <Card className="p-5 mb-2.5">
      <SectionLabel>DIAGNOSTICS</SectionLabel>
      <div className="flex gap-2 flex-wrap">
        <GhostButton onClick={() => reveal('logs')}>Reveal logs folder</GhostButton>
        <GhostButton onClick={() => reveal('config')}>Reveal config file</GhostButton>
        <GhostButton>Copy diagnostic info</GhostButton>
      </div>
      {error && (
        <div className="mt-3 text-[11px] text-neutral-400">{error}</div>
      )}
    </Card>
  );
}

function UpdatesSection() {
  // Five states the backend can return + four UI states: idle, busy,
  // result-OK, result-error. The endpoint returns 503
  // ``update_check_disabled`` when ``Settings.update_check_repo`` is
  // unset (no public release feed for this build). Previously the
  // card hid itself silently when that happened — the user clicked
  // the button and the whole card vanished with no feedback. Now we
  // surface an inline "update checks aren't configured for this
  // build" message so the user knows the click was acknowledged.
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null); // { status, current, latest, release_url, detail }
  const [error, setError] = useState(null);
  const [disabled, setDisabled] = useState(false);

  async function handleCheck() {
    setBusy(true);
    setError(null);
    setDisabled(false);
    try {
      const r = await checkForUpdates();
      setResult(r);
    } catch (err) {
      if (err instanceof ApiError && err.problem?.code === 'update_check_disabled') {
        setDisabled(true);
      } else {
        setError(err);
      }
    } finally {
      setBusy(false);
    }
  }

  function openReleasePage() {
    if (!result?.release_url) return;
    // pywebview exposes a JS API for opening URLs in the user's default
    // browser. When running in Vite dev (no pywebview), fall back to a
    // plain window.open with noopener — clicking the link in the dev
    // browser still works.
    const api = pywebviewApi();
    if (api && typeof api.open_url === 'function') {
      api.open_url(result.release_url);
    } else {
      window.open(result.release_url, '_blank', 'noopener,noreferrer');
    }
  }

  return (
    <Card className="p-5 mb-2.5">
      <div className="flex items-center justify-between mb-3">
        <SectionLabel>UPDATES</SectionLabel>
        {result && (
          <div className="flex items-center gap-1.5">
            <StatusDot
              status={
                result.status === 'up_to_date' ? 'green'
                  : result.status === 'update_available' ? 'yellow'
                  : 'neutral'
              }
            />
            <span
              className="text-[11px] font-medium"
              style={{
                color:
                  result.status === 'up_to_date' ? 'var(--status-ready)'
                    : result.status === 'update_available' ? 'var(--status-watch)'
                    : 'var(--text-faint)',
              }}
            >
              {result.status === 'up_to_date' && 'Up to date'}
              {result.status === 'update_available' && 'Update available'}
              {result.status === 'no_releases' && 'No releases yet'}
              {result.status === 'rate_limited' && 'Rate limited'}
              {result.status === 'error' && "Couldn't check"}
            </span>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2.5 flex-wrap">
        <button
          onClick={handleCheck}
          disabled={busy}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-[12px] rounded-lg transition disabled:opacity-50"
          style={{ background: 'transparent', color: 'var(--text)', border: '0.5px solid var(--border-strong)' }}
        >
          {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />}
          {busy ? 'Checking…' : 'Check for updates'}
        </button>
        <span className="text-[11px] text-neutral-500">
          Asks GitHub for the latest release. Downloads happen in your browser — no automatic install (yet).
        </span>
      </div>

      {result && result.status === 'update_available' && (
        <div
          className="mt-3 p-3 rounded-lg flex items-start gap-2.5"
          style={{ background: 'var(--status-watch-bg)', border: '0.5px solid var(--status-watch)' }}
        >
          <Download className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: 'var(--status-watch)' }} />
          <div className="flex-1 min-w-0">
            <div className="text-[12px] text-neutral-100">
              <span className="font-mono">{result.latest}</span> is available
              {result.current ? (
                <> (you have <span className="font-mono">{result.current}</span>)</>
              ) : null}
            </div>
            <div className="text-[11px] text-neutral-500 mt-0.5">
              Open the release page to download the new version.
            </div>
          </div>
          <button
            onClick={openReleasePage}
            className="px-2.5 py-1 text-[11px] font-medium rounded-md transition flex items-center gap-1 flex-shrink-0"
            style={{ background: 'var(--status-watch)', color: 'var(--bg)' }}
          >
            <ExternalLink className="w-3 h-3" />
            Open release page
          </button>
        </div>
      )}

      {result && result.status === 'up_to_date' && (
        <div className="mt-3 text-[11px] text-neutral-500">
          You're running <span className="font-mono text-neutral-300">{result.current}</span>, the latest release.
        </div>
      )}

      {disabled && (
        <div
          className="mt-3 p-3 rounded-lg flex items-start gap-2"
          style={{ background: 'var(--surface-2)', border: '0.5px solid var(--border)' }}
        >
          <AlertTriangle
            className="w-3.5 h-3.5 flex-shrink-0 mt-0.5"
            style={{ color: 'var(--text-muted)' }}
          />
          <div className="text-[12px] text-neutral-400 leading-relaxed">
            Update checks aren't configured for this build. To enable
            them, set <span className="font-mono text-neutral-300">update_check_repo</span>{' '}
            in <span className="font-mono text-neutral-300">config.toml</span> to a
            GitHub repo (e.g. <span className="font-mono text-neutral-300">owner/skydive-logbook</span>).
          </div>
        </div>
      )}

      {result
        && (result.status === 'no_releases'
            || result.status === 'rate_limited'
            || result.status === 'error')
        && result.detail && (
        <div className="mt-3 text-[11px] text-neutral-500">
          {result.detail}
        </div>
      )}

      {error && (
        <div
          className="mt-3 p-3 rounded-lg flex items-start gap-2"
          style={{ background: 'rgba(248,113,113,0.05)', border: '0.5px solid rgba(248,113,113,0.25)' }}
        >
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" style={{ color: 'var(--status-critical)' }} />
          <div className="text-[12px] text-neutral-300">
            {error instanceof ApiError
              ? (error.problem?.detail || error.message)
              : error.message}
          </div>
        </div>
      )}
    </Card>
  );
}


function AboutSection() {
  return (
    <Card className="p-5">
      <SectionLabel>ABOUT</SectionLabel>
      <div className="grid grid-cols-[110px_1fr] gap-1.5 text-[12px]">
        <span className="text-neutral-500">App version</span>
        <span className="text-neutral-300 font-mono">skydive-logbook 0.1.0-beta.1</span>
        <span className="text-neutral-500">Schema</span>
        <span className="text-neutral-300 font-mono">v1</span>
        <span className="text-neutral-500">License</span>
        <span className="text-neutral-300">GPL-3.0</span>
        <span className="text-neutral-500">Source</span>
        <span className="text-neutral-300 font-mono">github.com/Undefined6799/skydive-logbook</span>
      </div>
    </Card>
  );
}

function Segmented({ value, options, onChange, disabled = false }) {
  return (
    <div
      className="inline-flex gap-0.5 p-0.5 rounded-lg justify-self-start"
      style={{
        background: 'var(--bg)',
        border: '0.5px solid var(--border-strong)',
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {options.map((opt) => {
        const active = opt === value;
        return (
          <button
            key={opt}
            type="button"
            onClick={() => !disabled && onChange && onChange(opt)}
            disabled={disabled}
            className="px-2.5 py-1 rounded-md text-[11px] transition disabled:cursor-not-allowed"
            style={{
              background: active ? 'var(--surface-3)' : 'transparent',
              color: active ? 'var(--text)' : 'var(--text-faint)',
            }}
          >
            {opt}
          </button>
        );
      })}
    </div>
  );
}
