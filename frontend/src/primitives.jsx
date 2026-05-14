// Shared visual atoms used across views. Keeps the dark palette,
// status-dot, and KPI patterns consistent.
//
// The STATUS map is the single point of color truth for ready/watch
// /action/critical states. Existing call sites use the legacy keys
// (green/yellow/red/neutral); those are aliased onto the design-
// system pastels so view files keep working until they migrate to
// the new keys (ready/watch/action/critical) in later phases.

import React from 'react';

// Pastel design-system palette — see reviews/design-system/design-language.md
// and the --status-* CSS variables in index.css.
const READY    = { dot: '#a8d5b5', bg: 'rgba(168,213,181,0.10)', border: 'rgba(168,213,181,0.30)', text: '#a8d5b5', glow: 'none' };
const WATCH    = { dot: '#ddcb8c', bg: 'rgba(221,203,140,0.10)', border: 'rgba(221,203,140,0.30)', text: '#ddcb8c', glow: 'none' };
const ACTION   = { dot: '#ddb494', bg: 'rgba(221,180,148,0.10)', border: 'rgba(221,180,148,0.30)', text: '#ddb494', glow: 'none' };
const CRITICAL = { dot: '#d9a8a8', bg: 'rgba(217,168,168,0.10)', border: 'rgba(217,168,168,0.30)', text: '#d9a8a8', glow: 'none' };
const NEUTRAL  = { dot: '#5a6478', bg: 'transparent', border: '#2a3245', text: '#8b97aa', glow: 'none' };

export const STATUS = {
  // Design-system keys (preferred for new code).
  ready: READY, watch: WATCH, action: ACTION, critical: CRITICAL, neutral: NEUTRAL,
  // Legacy keys still in use by views/modals; kept stable until phase-3 migration.
  green: READY, yellow: WATCH, red: CRITICAL,
};

export function StatusDot({ status = 'green', size = 'sm' }) {
  const s = STATUS[status] || STATUS.green;
  const dim = size === 'lg' ? 10 : size === 'md' ? 8 : 7;
  return (
    <span
      className="inline-block rounded-full flex-shrink-0"
      style={{ width: dim, height: dim, background: s.dot, boxShadow: s.glow }}
    />
  );
}

export function ClockPill({ label, days }) {
  const active = days !== null && days !== undefined;
  const urgent = active && days < 30;
  const warning = active && days < 60 && !urgent;
  // Pastel design-system colors (--status-critical / --status-watch / --text)
  const color = urgent ? '#d9a8a8' : warning ? '#ddcb8c' : '#e8edf3';
  return (
    <div
      className="rounded-xl px-4 py-2.5 min-w-[120px]"
      style={{ borderWidth: '0.5px', borderStyle: 'solid', borderColor: active ? 'var(--border)' : 'var(--accent-soft-border)', background: 'var(--surface-1)' }}
    >
      <div className="text-[9px] tracking-[0.25em] font-medium mb-1" style={{ color: active ? 'var(--text-muted)' : 'var(--text-faint)' }}>
        {label}
      </div>
      {active ? (
        <div className="flex items-baseline gap-1.5">
          <span className="text-2xl font-mono font-medium" style={{ color }}>{days}</span>
          <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>days</span>
        </div>
      ) : (
        <div className="text-xs italic pt-1" style={{ color: 'var(--text-faint)' }}>not sealed</div>
      )}
    </div>
  );
}

export function StatCard({ label, value, mono, badge, badgeColor, status, sub }) {
  const s = status ? STATUS[status] : null;
  return (
    <div
      className="rounded-xl px-4 py-3"
      style={{
        background: 'var(--surface-1)',
        border: `0.5px solid ${s ? s.border : 'var(--border)'}`,
      }}
    >
      <div
        className="text-[9px] tracking-[0.25em] font-medium mb-1.5"
        style={{ color: 'var(--text-muted)' }}
      >
        {label}
      </div>
      <div className="flex items-center gap-2">
        <span
          className={`text-xl ${mono ? 'font-mono' : ''} font-medium`}
          style={{ color: s ? s.text : 'var(--text)' }}
        >
          {value}
        </span>
        {badge && (
          <span
            className="flex items-center gap-1 text-[9px] font-medium px-1.5 py-0.5 rounded-full"
            style={{
              color: badgeColor === 'amber' ? 'var(--status-watch)' : 'var(--text-muted)',
              background: badgeColor === 'amber' ? 'rgba(221,203,140,0.10)' : 'var(--surface-3)',
              border: `0.5px solid ${badgeColor === 'amber' ? 'rgba(221,203,140,0.30)' : 'var(--border)'}`,
            }}
          >
            {badge}
          </span>
        )}
      </div>
      {sub && <div className="text-[10px] mt-1" style={{ color: 'var(--text-faint)' }}>{sub}</div>}
    </div>
  );
}

export function ProgressRow({ label, value, max, mono = true }) {
  const pct = Math.min(100, (value / max) * 100);
  // Pastel design-system colors (--status-critical / --status-watch / --status-ready)
  const color = pct > 90 ? '#d9a8a8' : pct > 75 ? '#ddcb8c' : '#a8d5b5';
  return (
    <div>
      <div className="flex justify-between text-[11px] mb-1">
        <span style={{ color: 'var(--text-muted)' }}>{label}</span>
        <span
          className={mono ? 'font-mono' : ''}
          style={{ color: 'var(--text)' }}
        >
          {value}/{max}
        </span>
      </div>
      <div
        className="h-1 rounded-full overflow-hidden"
        style={{ background: 'var(--surface-3)' }}
      >
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
    </div>
  );
}

export function SectionLabel({ children, accent }) {
  return (
    <div
      className="text-[10px] tracking-[0.25em] font-medium mb-3"
      style={{ color: accent || 'var(--text-muted)' }}
    >
      {children}
    </div>
  );
}

export function PrimaryButton({ children, onClick, ...rest }) {
  return (
    <button
      onClick={onClick}
      className="px-4 py-2 text-[13px] font-medium rounded-lg flex items-center gap-2 transition"
      style={{ background: 'var(--accent)', color: 'var(--accent-ink)' }}
      onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--accent-hover)')}
      onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--accent)')}
      {...rest}
    >
      {children}
    </button>
  );
}

export function GhostButton({ children, onClick, ...rest }) {
  return (
    <button
      onClick={onClick}
      className="bg-transparent px-3 py-2 text-[12px] rounded-lg flex items-center gap-2 transition border"
      style={{ borderColor: 'var(--border)', color: 'var(--text)' }}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Card({ children, accent, className = '', style = {} }) {
  return (
    <div
      className={`rounded-xl ${className}`}
      style={{
        background: 'var(--surface-1)',
        border: `0.5px solid ${accent || 'var(--border)'}`,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export function disciplinePill(discipline) {
  return discipline;
}
