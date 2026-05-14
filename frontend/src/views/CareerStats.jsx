import React, { useEffect, useState } from 'react';
import { getStats } from '../api';
import { SectionLabel, Card } from '../primitives';

// Career-wide stats panel (P.3). Lifted from Jumps.jsx so the
// Profile tab can render it as the user's landing surface; the
// Jumps page no longer carries a Stats tab — the career-stats
// subtitle there is enough context.
//
// Self-fetches on mount via GET /api/v1/stats. Re-fetches when
// ``reloadKey`` changes (a parent can bump it after creating /
// editing / deleting a jump). The bare ``Card`` skeletons render
// while the request is in flight; an error is rendered inline so
// the parent's layout doesn't shift.

export default function CareerStats({ reloadKey = 0 }) {
  const [stats, setStats] = useState(null);     // null → loading
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getStats()
      .then((s) => { if (!cancelled) setStats(s); })
      .catch((err) => { if (!cancelled) setError(err); });
    return () => { cancelled = true; };
  }, [reloadKey]);

  if (error) {
    return (
      <div className="text-[12px] text-amber-300">
        Couldn't load career stats: {String(error.message || error)}
      </div>
    );
  }
  if (stats === null) {
    return <StatsSkeleton />;
  }

  const {
    total, this_year, last_90_days, days_since_last_jump,
    freefall_seconds, year_by_month, by_discipline, by_dropzone,
  } = stats;
  // last_90_days is intentionally read but not displayed in the
  // top StatCard cluster — keep destructure to flag it as known
  // shape for future use.
  void last_90_days;

  const maxMonth = Math.max(...year_by_month, 1);
  const monthLetters = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];
  const currentYear = new Date().getFullYear();
  const currentMonthIdx = new Date().getMonth();

  // Currency banding mirrors USPA's general A-license rule (1 jump
  // every 60 days). Green ≤ 60d, yellow 60–180d, red ≥ 180d. The
  // empty-logbook case (no jumps yet) renders as neutral.
  let currencyStatus = 'neutral';
  let currencyLabel = 'No jumps yet';
  let currencySub = 'Log your first jump to start tracking';
  if (days_since_last_jump !== null) {
    if (days_since_last_jump <= 60) {
      currencyStatus = 'green';
      currencyLabel = 'Active';
    } else if (days_since_last_jump <= 180) {
      currencyStatus = 'yellow';
      currencyLabel = 'Lapsing';
    } else {
      currencyStatus = 'red';
      currencyLabel = 'Lapsed';
    }
    currencySub = `${days_since_last_jump} ${days_since_last_jump === 1 ? 'day' : 'days'} since last jump`;
  }

  // Average freefall time per jump that recorded one. Skip when total = 0.
  const avgFreefall = total > 0 && freefall_seconds > 0
    ? Math.round(freefall_seconds / total)
    : null;

  // Monthly target — matches the mockup's "Monthly target (5)"
  // dashed reference line. The chart axis is scaled to whichever is
  // larger so the target line is always visible.
  const monthlyTarget = 5;
  const chartMax = Math.max(maxMonth, monthlyTarget, 1);
  return (
    <>
      <div className="grid grid-cols-4 gap-4 mb-4">
        <HeroMetric
          label="Total jumps"
          value={total}
          foot={this_year > 0 ? `+${this_year} this year` : 'no jumps this year'}
        />
        <HeroMetric
          label="Freefall"
          value={formatFreefall(freefall_seconds)}
          foot={avgFreefall != null ? `avg ${avgFreefall}s / jump` : '—'}
        />
        <HeroMetric
          label="This year"
          value={this_year}
          foot={`since Jan 1, ${currentYear}`}
        />
        <HeroMetric
          label="Currency"
          value={currencyLabel}
          valueStatus={currencyStatus}
          foot={currencySub}
        />
      </div>

      <Card className="p-6 mb-3">
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
          {/* Dashed target line at monthly target. */}
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
            {year_by_month.map((v, i) => {
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

      <div className="grid grid-cols-2 gap-3 mb-3">
        <BarChartCard label="BY DISCIPLINE" data={by_discipline} labelWidth={80} emptyLabel="No discipline tags yet" />
        <BarChartCard label="BY DROPZONE" data={by_dropzone} labelWidth={130} emptyLabel="No jumps logged yet" />
      </div>

      <div className="text-[11px] text-neutral-500 mt-2">
        By-canopy and by-rig breakdowns arrive with the rig manager.
      </div>
    </>
  );
}


// Hero metric tile — matches reviews/design-system/redesign-profile.html.
// Big mono number (32px) on a card, with a small uppercase label above
// and a foot line below. The currency value gets a status color (sage
// green for active, cream for lapsing, rose for lapsed).
function HeroMetric({ label, value, valueStatus, foot }) {
  const color =
    valueStatus === 'green' ? 'var(--status-ready)'
    : valueStatus === 'yellow' ? 'var(--status-watch)'
    : valueStatus === 'red' ? 'var(--status-critical)'
    : 'var(--text)';
  // The currency tile renders the status word in the UI font for
  // legibility; everything else uses mono for the data feel.
  const isStatusWord = valueStatus && valueStatus !== 'neutral';
  return (
    <div
      className="rounded-xl p-5"
      style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border)' }}
    >
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
        {value}
      </div>
      {foot && (
        <div className="text-[11px] mt-2" style={{ color: 'var(--text-faint)' }}>
          {foot}
        </div>
      )}
    </div>
  );
}


export function formatFreefall(totalSeconds) {
  if (!totalSeconds) return '0m';
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes >= 60) {
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return `${h}h ${m}m`;
  }
  if (minutes > 0) return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
  return `${seconds}s`;
}


function StatsSkeleton() {
  return (
    <>
      <div className="grid grid-cols-4 gap-2 mb-3">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="rounded-xl p-4" style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)', opacity: 0.5 }}>
            <div className="h-3 rounded mb-3" style={{ background: 'var(--surface-2)', width: 80 }} />
            <div className="h-6 rounded" style={{ background: 'var(--surface-2)', width: 60 }} />
          </div>
        ))}
      </div>
      <Card className="p-5 mb-3" style={{ height: 160, opacity: 0.5 }}>
        <div className="h-3 rounded" style={{ background: 'var(--surface-2)', width: 200 }} />
      </Card>
    </>
  );
}


function BarChartCard({ label, data, labelWidth, emptyLabel }) {
  if (!data || data.length === 0) {
    return (
      <Card className="p-5">
        <SectionLabel>{label}</SectionLabel>
        <div className="text-[12px] text-neutral-500 italic">
          {emptyLabel || 'No data yet'}
        </div>
      </Card>
    );
  }
  const max = Math.max(...data.map((d) => d[1]));
  return (
    <Card className="p-5">
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
