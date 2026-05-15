import React from 'react';
import { AlertTriangle } from 'lucide-react';
import { ApiError } from '../../api';

// Shared atoms for every wizard step's form. Lifted out of the
// modal-internal versions so a future tweak to spacing / typography
// applies to the whole wizard in one place. Matches the visual
// language of AddComponentModal (the per-modal copies remain
// because each modal also wires its own header / footer that the
// wizard does not reuse).

export const inputCls =
  'w-full rounded-md px-3 py-1.5 text-[13px] text-neutral-100 ' +
  'bg-[var(--bg)] border border-neutral-800 ' +
  'focus:border-neutral-600 focus:outline-none';

export function Section({ label, children }) {
  return (
    <div>
      <div className="text-[10px] tracking-[0.25em] text-neutral-500 font-medium mb-2">
        {label}
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

export function FormGrid({ children }) {
  return <div className="grid grid-cols-2 gap-3">{children}</div>;
}

export function Field({ label, required, children }) {
  return (
    <div>
      <div className="text-[9px] tracking-[0.2em] text-neutral-500 font-medium mb-1 flex items-center gap-1">
        <span>{label}</span>
        {required && <span style={{ color: 'var(--status-critical)' }}>*</span>}
      </div>
      {children}
    </div>
  );
}

export function ErrorBanner({ error }) {
  let message = String(error?.message || error);
  let pointers = [];
  if (error instanceof ApiError && error.problem) {
    message = error.problem.detail || error.problem.title || message;
    if (Array.isArray(error.problem.errors)) {
      pointers = error.problem.errors;
    }
  }
  return (
    <div
      className="flex items-start gap-2 rounded-lg px-3.5 py-2.5 mb-4 text-[12px]"
      style={{
        background: 'rgba(217,168,168,0.08)',
        border: '0.5px solid rgba(217,168,168,0.30)',
        color: '#d9a8a8',
      }}
    >
      <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div>{message}</div>
        {pointers.length > 0 && (
          <div className="mt-1.5 space-y-0.5">
            {pointers.map((p, i) => (
              <div key={i} className="text-[11px] text-neutral-400">
                <span className="font-mono">{p.pointer}</span>: {p.detail}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
