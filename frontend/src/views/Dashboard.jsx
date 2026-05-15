import React, { useEffect, useState, useMemo } from 'react';
import {
  Plus,
  Layers,
  MapPin,
  Sliders,
  AlertTriangle,
  CalendarDays,
  Timer,
  Activity,
  ListChecks,
} from 'lucide-react';
import { getStats, listJumps, ApiError } from '../api';
import { PrimaryButton, GhostButton, Card, SectionLabel } from '../primitives';
import { formatFreefall } from './CareerStats';
import LogJumpModal from '../modals/LogJumpModal';

// Dashboard — landing page replacing the legacy Profile tab.
//
// Two surfaces:
//   1. Function bar — quick action buttons (currently: Log a jump).
//      Designed to host more shortcuts (Add rig, Add dropzone) as
//      they're requested; keep the contract self-evident so adding
//      a new shortcut is a one-liner in FUNCTION_BAR_ACTIONS.
//   2. Widget grid — a configurable mosaic of stats tiles. The user
//      picks which widgets show via the "Customize" affordance; the
//      selection persists in localStorage. Widgets are rendered in
//      their declared order from WIDGETS so the grid stays stable
//      across sessions.
//
// All widgets share a single stats fetch (one /api/v1/stats round
// trip per mount + per reload) and a single recent-jumps fetch. The
// "Log a jump" success callback bumps reloadKey so every widget
// re-fetches.

const LS_KEY = 'dashboard.widgets.v1';

// Widget catalog. ``size`` controls grid span (1=small, 2=medium,
// 3=wide). ``render`` receives ({ stats, recentJumps }) and returns
// the widget body inside its own Card. ``defaultEnabled`` controls
// what shows on first load before the user customizes.
const WIDGETS = [
  {
    id: 'total',
    label: 'Total jumps',
    size: 1,
    defaultEnabled: true,
    render: ({ stats }) => (
      <Metric
        label="Total jumps"
        value={fmtInt(stats?.total)}
        foot={stats && stats.this_year > 0
          ? `+${stats.this_year} this year`
          : 'no jumps this year'}
      />
    ),
  },
  {
    id: 'currency',
    label: 'Currency',
    size: 1,
    defaultEnabled: true,
    render: ({ stats }) => {
      const c = currencyFromStats(stats);
      return (
        <Metric
          label="Currency"
          value={c.label}
          valueStatus={c.status}
          foot={c.sub}
        />
      );
    },
  },
  {
    id: 'freefall',
    label: 'Freefall',
    size: 1,
    defaultEnabled: true,
    render: ({ stats }) => {
      const avg = stats && stats.total > 0 && stats.freefall_seconds > 0
        ? Math.round(stats.freefall_seconds / stats.total) : null;
      return (
        <Metric
          label="Freefall"
          value={stats ? formatFreefall(stats.freefall_seconds) : '—'}
          foot={avg != null ? `avg ${avg}s / jump` : '—'}
        />
      );
    },
  },
  {
    id: 'this_year',
    label: 'This year',
    size: 1,
    defaultEnabled: true,
    render: ({ stats }) => (
      <Metric
        label="This year"
        value={fmtInt(stats?.this_year)}
        foot={`since Jan 1, ${new Date().getFullYear()}`}
      />
    ),
  },
  {
    id: 'last_90',
    label: 'Last 90 days',
    size: 1,
    defaultEnabled: false,
    render: ({ stats }) => (
      <Metric
        label="Last 90 days"
        value={fmtInt(stats?.last_90_days)}
        foot="rolling window"
      />
    ),
  },
  {
    id: 'monthly_chart',
    label: 'Jumps per month',
    size: 3,
    defaultEnabled: true,
    render: ({ stats }) => <MonthlyChartWidget stats={stats} />,
  },
  {
    id: 'discipline',
    label: 'By discipline',
    size: 2,
    defaultEnabled: false,
    render: ({ stats }) => (
      <BarChartWidget
        label="BY DISCIPLINE"
        data={stats?.by_discipline}
        labelWidth={80}
        emptyLabel="No discipline tags yet"
      />
    ),
  },
  {
    id: 'dropzone',
    label: 'By dropzone',
    size: 2,
    defaultEnabled: false,
    render: ({ stats }) => (
      <BarChartWidget
        label="BY DROPZONE"
        data={stats?.by_dropzone}
        labelWidth={130}
        emptyLabel="No jumps logged yet"
      />
    ),
  },
  {
    id: 'recent',
    label: 'Recent jumps',
    size: 2,
    defaultEnabled: false,
    render: ({ recentJumps }) => <RecentJumpsWidget jumps={recentJumps} />,
  },
];

const WIDGET_ICONS = {
  total: ListChecks,
  currency: Activity,
  freefall: Timer,
  this_year: CalendarDays,
  last_90: CalendarDays,
  monthly_chart: CalendarDays,
  discipline: Layers,
  dropzone: MapPin,
  recent: ListChecks,
};

function loadSelectedIds() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    // Filter to known widget ids so a stale localStorage entry from
    // a previous version doesn't render a ghost slot.
    const known = new Set(WIDGETS.map((w) => w.id));
    return parsed.filter((id) => known.has(id));
  } catch {
    return null;
  }
}

function defaultSelectedIds() {
  return WIDGETS.filter((w) => w.defaultEnabled).map((w) => w.id);
}

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [recentJumps, setRecentJumps] = useState(null);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [showLogModal, setShowLogModal] = useState(false);
  const [customizing, setCustomizing] = useState(false);
  const [selected, setSelected] = useState(() => loadSelectedIds() || defaultSelectedIds());

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getStats()
      .then((s) => { if (!cancelled) setStats(s); })
      .catch((err) => { if (!cancelled) setError(err); });
    listJumps({ limit: 5 })
      .then((j) => { if (!cancelled) setRecentJumps(j || []); })
      .catch(() => { /* tolerate — recent-jumps widget shows empty */ });
    return () => { cancelled = true; };
  }, [reloadKey]);

  // Persist selection on every change. Wrapped so a localStorage
  // failure (private mode, quota) doesn't crash the dashboard —
  // selection still works for the rest of the session.
  function setSelectedPersisted(next) {
    setSelected(next);
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(next));
    } catch {
      /* tolerate */
    }
  }

  function toggle(id) {
    if (selected.includes(id)) {
      setSelectedPersisted(selected.filter((x) => x !== id));
    } else {
      setSelectedPersisted([...selected, id]);
    }
  }

  function resetDefaults() {
    setSelectedPersisted(defaultSelectedIds());
  }

  function handleJumpCreated() {
    setReloadKey((k) => k + 1);
  }

  // Render widgets in their catalog order rather than selection order
  // so the grid stays stable when the user toggles widgets on/off.
  const activeWidgets = useMemo(
    () => WIDGETS.filter((w) => selected.includes(w.id)),
    [selected],
  );

  return (
    <div className="px-10 py-10 max-w-[1100px]">
      <div className="text-3xl font-medium tracking-tight mb-1">Dashboard</div>
      <div className="text-[12px] text-neutral-500 mb-6">
        Quick actions and the stats that matter to you.
      </div>

      <FunctionBar
        onLogJump={() => setShowLogModal(true)}
        onCustomize={() => setCustomizing((v) => !v)}
        customizing={customizing}
      />

      {customizing && (
        <WidgetChooser
          selected={selected}
          onToggle={toggle}
          onReset={resetDefaults}
        />
      )}

      {error && (
        <Card className="p-4 mt-4">
          <div className="flex items-start gap-2 text-[12px]" style={{ color: 'var(--status-critical)' }}>
            <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
            <div>
              {error instanceof ApiError
                ? (error.problem?.detail || error.message)
                : (error.message || 'Failed to load stats.')}
            </div>
          </div>
        </Card>
      )}

      <div
        className="mt-4 grid gap-3"
        style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}
      >
        {activeWidgets.length === 0 && (
          <div
            className="col-span-3 text-[12px] text-neutral-500 italic py-8 text-center rounded-lg"
            style={{ background: 'var(--surface-1)', border: '0.5px dashed var(--border-strong)' }}
          >
            No widgets enabled. Use Customize to pick what to show.
          </div>
        )}
        {activeWidgets.map((w) => (
          <div key={w.id} style={{ gridColumn: `span ${w.size} / span ${w.size}` }}>
            {w.render({ stats, recentJumps })}
          </div>
        ))}
      </div>

      <LogJumpModal
        visible={showLogModal}
        mode="create"
        onClose={() => setShowLogModal(false)}
        onCreated={handleJumpCreated}
        suggestedJumpNumber={
          recentJumps && recentJumps.length > 0
            ? Math.max(...recentJumps.map((j) => j.jump_number)) + 1
            : 1
        }
        lastDropzone={(recentJumps && recentJumps[0]?.dropzone) || ''}
      />
    </div>
  );
}


// --------------------------------------------------------------------- //
// Function bar
// --------------------------------------------------------------------- //

function FunctionBar({ onLogJump, onCustomize, customizing }) {
  return (
    <div
      className="rounded-xl p-3 flex items-center gap-2 flex-wrap"
      style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border)' }}
    >
      <PrimaryButton onClick={onLogJump}>
        <Plus className="w-4 h-4" strokeWidth={2.2} />
        Log a jump
      </PrimaryButton>

      <div className="flex-1" />

      <GhostButton onClick={onCustomize}>
        <Sliders className="w-3.5 h-3.5" />
        {customizing ? 'Done' : 'Customize'}
      </GhostButton>
    </div>
  );
}


// --------------------------------------------------------------------- //
// Widget chooser
// --------------------------------------------------------------------- //

function WidgetChooser({ selected, onToggle, onReset }) {
  return (
    <Card className="p-4 mt-3">
      <div className="flex items-center justify-between mb-3">
        <SectionLabel>WIDGETS</SectionLabel>
        <button
          onClick={onReset}
          className="text-[11px] underline-offset-2 hover:underline"
          style={{ color: 'var(--text-muted)' }}
        >
          Reset to defaults
        </button>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
        {WIDGETS.map((w) => {
          const Icon = WIDGET_ICONS[w.id] || ListChecks;
          const on = selected.includes(w.id);
          return (
            <button
              key={w.id}
              onClick={() => onToggle(w.id)}
              className="flex items-center gap-2.5 px-3 py-2 rounded-lg text-[12px] transition text-left"
              style={{
                background: on ? 'var(--surface-3)' : 'transparent',
                border: `0.5px solid ${on ? 'var(--accent-soft-border)' : 'var(--border)'}`,
                color: on ? 'var(--text)' : 'var(--text-muted)',
              }}
            >
              <Icon className="w-3.5 h-3.5 flex-shrink-0" />
              <span className="flex-1 truncate">{w.label}</span>
              <span
                className="w-3.5 h-3.5 rounded flex items-center justify-center text-[10px] font-mono flex-shrink-0"
                style={{
                  background: on ? 'var(--accent)' : 'transparent',
                  color: on ? 'var(--accent-ink)' : 'transparent',
                  border: `0.5px solid ${on ? 'var(--accent)' : 'var(--border-strong)'}`,
                }}
              >
                {on ? '✓' : ''}
              </span>
            </button>
          );
        })}
      </div>
    </Card>
  );
}


// --------------------------------------------------------------------- //
// Widget primitives
// --------------------------------------------------------------------- //

function Metric({ label, value, valueStatus, foot }) {
  const color =
    valueStatus === 'green' ? 'var(--status-ready)'
    : valueStatus === 'yellow' ? 'var(--status-watch)'
    : valueStatus === 'red' ? 'var(--status-critical)'
    : 'var(--text)';
  const isStatusWord = valueStatus && valueStatus !== 'neutral';
  return (
    <Card className="p-5 h-full">
      <div
        className="text-[11px] font-medium mb-3"
        style={{ color: 'var(--text-muted)', letterSpacing: '0.08em', textTransform: 'uppercase' }}
      >
        {label}
      </div>
      <div
        className={isStatusWord ? '' : 'font-mono'}
        style={{
          fontSize: isStatusWord ? 24 : 30,
          fontWeight: 500,
          color,
          letterSpacing: '-0.01em',
          lineHeight: 1.1,
        }}
      >
        {value ?? '—'}
      </div>
      {foot && (
        <div className="text-[11px] mt-2" style={{ color: 'var(--text-faint)' }}>
          {foot}
        </div>
      )}
    </Card>
  );
}


function MonthlyChartWidget({ stats }) {
  const monthLetters = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];
  const currentYear = new Date().getFullYear();
  const currentMonthIdx = new Date().getMonth();
  const data = stats?.year_by_month || new Array(12).fill(0);
  const monthlyTarget = 5;
  const maxMonth = Math.max(...data, 1);
  const chartMax = Math.max(maxMonth, monthlyTarget, 1);

  return (
    <Card className="p-6">
      <div className="flex items-baseline justify-between mb-5">
        <SectionLabel>JUMPS PER MONTH · {currentYear}</SectionLabel>
        <div className="flex items-center gap-4 text-[11px]" style={{ color: 'var(--text-muted)' }}>
          <span className="inline-flex items-center gap-1.5">
            <span style={{ display: 'inline-block', width: 14, height: 10, borderRadius: 2, background: 'rgba(168,197,220,0.7)' }} />
            Past month
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span style={{ display: 'inline-block', width: 14, height: 10, borderRadius: 2, border: '1.5px dashed var(--accent)' }} />
            Current
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span style={{ display: 'inline-block', width: 18, height: 0, borderTop: '1.5px dashed var(--text-muted)' }} />
            Monthly target ({monthlyTarget})
          </span>
        </div>
      </div>
      <div className="relative" style={{ height: 140 }}>
        <div
          aria-hidden="true"
          style={{
            position: 'absolute', left: 0, right: 0,
            bottom: `${(monthlyTarget / chartMax) * 100}%`,
            height: 1, borderTop: '1px dashed var(--text-faint)',
            pointerEvents: 'none',
          }}
        />
        <div
          className="grid grid-cols-12 gap-3 items-end"
          style={{ height: '100%', borderBottom: '1px solid var(--border)' }}
        >
          {data.map((v, i) => {
            const isCurrent = i === currentMonthIdx;
            const heightPct = v > 0 ? `${Math.max(2, (v / chartMax) * 100)}%` : 0;
            return (
              <div
                key={i}
                className="rounded-sm"
                style={{
                  height: heightPct || 4,
                  background: isCurrent ? 'transparent' : 'rgba(168,197,220,0.7)',
                  border: isCurrent ? '1.5px dashed var(--accent)' : 'none',
                }}
                title={`${monthLetters[i]}: ${v} ${v === 1 ? 'jump' : 'jumps'}`}
              />
            );
          })}
        </div>
      </div>
      <div className="grid grid-cols-12 gap-3 mt-2 text-[11px] text-center font-mono" style={{ color: 'var(--text-faint)' }}>
        {monthLetters.map((m, i) => (
          <span key={i} style={{ color: i === currentMonthIdx ? 'var(--text)' : undefined }}>
            {m}
          </span>
        ))}
      </div>
    </Card>
  );
}


function BarChartWidget({ label, data, labelWidth, emptyLabel }) {
  if (!data || data.length === 0) {
    return (
      <Card className="p-5 h-full">
        <SectionLabel>{label}</SectionLabel>
        <div className="text-[12px] text-neutral-500 italic">
          {emptyLabel || 'No data yet'}
        </div>
      </Card>
    );
  }
  const max = Math.max(...data.map((d) => d[1]));
  return (
    <Card className="p-5 h-full">
      <SectionLabel>{label}</SectionLabel>
      <div className="flex flex-col gap-2.5">
        {data.map(([name, value]) => (
          <div
            key={name}
            className="grid items-center gap-2.5 text-[12px]"
            style={{ gridTemplateColumns: `${labelWidth}px 1fr 32px` }}
          >
            <span className="text-neutral-400 truncate" title={name}>{name}</span>
            <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
              <div className="h-full" style={{ width: `${(value / max) * 100}%`, background: 'var(--text-faint)' }} />
            </div>
            <span className="text-right font-mono text-neutral-300">{value}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}


function RecentJumpsWidget({ jumps }) {
  if (jumps === null) {
    return (
      <Card className="p-5 h-full">
        <SectionLabel>RECENT JUMPS</SectionLabel>
        <div className="text-[12px] text-neutral-500">Loading…</div>
      </Card>
    );
  }
  if (jumps.length === 0) {
    return (
      <Card className="p-5 h-full">
        <SectionLabel>RECENT JUMPS</SectionLabel>
        <div className="text-[12px] text-neutral-500 italic">
          No jumps logged yet.
        </div>
      </Card>
    );
  }
  return (
    <Card className="p-5 h-full">
      <SectionLabel>RECENT JUMPS</SectionLabel>
      <div className="flex flex-col gap-2">
        {jumps.slice(0, 5).map((j) => (
          <div key={j.id} className="flex items-baseline justify-between gap-3 text-[12px]">
            <div className="flex items-baseline gap-2 min-w-0">
              <span className="text-neutral-500 font-mono text-[11px]">#{j.jump_number}</span>
              <span className="text-neutral-200 truncate">
                {j.title || j.dropzone || '—'}
              </span>
            </div>
            <span className="text-neutral-500 font-mono text-[11px] flex-shrink-0">
              {j.date}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}


// --------------------------------------------------------------------- //
// Helpers
// --------------------------------------------------------------------- //

function fmtInt(v) {
  return v == null ? '—' : String(v);
}

// Currency banding mirrors CareerStats — USPA general A-license rule
// (1 jump every 60 days). Green ≤ 60d, yellow 60–180d, red > 180d.
function currencyFromStats(stats) {
  if (!stats || stats.days_since_last_jump == null) {
    return { label: 'No jumps yet', status: 'neutral', sub: 'Log your first jump to start tracking' };
  }
  const d = stats.days_since_last_jump;
  let status = 'green';
  let label = 'Active';
  if (d > 180) { status = 'red'; label = 'Lapsed'; }
  else if (d > 60) { status = 'yellow'; label = 'Lapsing'; }
  return {
    label,
    status,
    sub: `${d} ${d === 1 ? 'day' : 'days'} since last jump`,
  };
}

