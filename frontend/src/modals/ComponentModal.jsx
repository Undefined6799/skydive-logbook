import React, { useState, useEffect } from 'react';
import { X, ArrowRightLeft, Plus, Circle, Info, ChevronDown, MessageSquare, Send } from 'lucide-react';
import { StatusDot, STATUS } from '../primitives';
import { unassignedComponents } from '../mock';

const TYPE_LABELS = { main: 'Main canopy', reserve: 'Reserve', aad: 'AAD', container: 'Container' };

export default function ComponentModal({ type, data, onClose }) {
  const [showSwap, setShowSwap] = useState(false);
  const [showNotes, setShowNotes] = useState(false);

  useEffect(() => {
    if (type) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
      setShowSwap(false);
      setShowNotes(false);
    }
    return () => { document.body.style.overflow = ''; };
  }, [type]);

  if (!type || !data) return null;

  const canJumperSwap = type === 'main';
  const swapOptions = type === 'main' ? unassignedComponents.filter((c) => c.type === 'main') : [];

  return (
    <>
      <div
        onClick={onClose}
        className="fixed inset-0 z-40 transition-opacity duration-200"
        style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)' }}
      />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-6 pointer-events-none">
        <div
          onClick={(e) => e.stopPropagation()}
          className="rounded-2xl w-full max-w-2xl max-h-[85vh] overflow-hidden flex flex-col pointer-events-auto"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)' }}
        >
          <div className="flex items-start justify-between px-6 pt-6 pb-4" style={{ borderBottom: '0.5px solid var(--border-strong)' }}>
            <div>
              <div className="flex items-center gap-2 mb-1.5">
                <StatusDot status={data.status} />
                <span className="text-[10px] tracking-[0.25em] text-neutral-400 font-medium">
                  {TYPE_LABELS[type].toUpperCase()}
                </span>
              </div>
              <div className="text-[22px] font-medium tracking-tight">
                {type === 'aad' ? data.model : `${data.model}${data.size ? ' ' + data.size : ''}`}
              </div>
              <div className="text-[12px] text-neutral-500 mt-1">{data.brand}</div>
            </div>
            <button
              onClick={onClose}
              className="w-8 h-8 rounded-lg flex items-center justify-center transition hover:bg-neutral-800"
              style={{ background: 'var(--surface-2)' }}
            >
              <X className="w-4 h-4 text-neutral-400" />
            </button>
          </div>

          <div className="overflow-y-auto flex-1 p-5 space-y-3">
            <Card>
              <Stats type={type} data={data} />
            </Card>

            {type === 'main' && (
              <Card>
                <div className="flex items-center justify-between mb-2.5">
                  <span className="text-[10px] tracking-[0.25em] text-neutral-500 font-medium">
                    LINE STRENGTH REMAINING
                  </span>
                  <span className="font-mono text-[15px]">{data.lineset.remaining}%</span>
                </div>
                <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
                  <div
                    className="h-full transition-all duration-500"
                    style={{
                      width: `${data.lineset.remaining}%`,
                      background:
                        data.lineset.remaining > 30 ? 'var(--status-ready)' : data.lineset.remaining > 10 ? 'var(--status-watch)' : 'var(--status-critical)',
                    }}
                  />
                </div>
                <div className="text-[11px] text-neutral-500 mt-2">
                  Yellow at 10% remaining · Reline triggers a fresh lineset record.
                </div>
              </Card>
            )}

            {type === 'aad' && (
              <Card>
                <div className="text-[10px] tracking-[0.25em] text-neutral-500 font-medium mb-1.5">
                  NEXT ACTION
                </div>
                <div className="text-[15px] font-medium">{data.nextAction}</div>
                <div className="text-[12px] text-neutral-500 font-mono mt-1">in {data.daysToAction} days</div>
              </Card>
            )}

            {type !== 'aad' && (
              <Notes notes={data.notes || []} expanded={showNotes} onToggle={() => setShowNotes(!showNotes)} />
            )}

            {canJumperSwap ? (
              <div>
                <button
                  onClick={() => setShowSwap(!showSwap)}
                  className="w-full rounded-xl py-3 text-[13px] font-medium flex items-center justify-center gap-2 transition"
                  style={{ background: 'var(--text)', color: 'var(--bg)' }}
                >
                  <ArrowRightLeft className="w-4 h-4" />
                  Change main canopy
                </button>
                <div
                  className="overflow-hidden transition-all duration-300"
                  style={{
                    maxHeight: showSwap ? 400 : 0,
                    marginTop: showSwap ? 12 : 0,
                    opacity: showSwap ? 1 : 0,
                  }}
                >
                  <div className="text-[10px] tracking-[0.25em] text-neutral-500 font-medium mb-2 px-1">
                    AVAILABLE IN INVENTORY
                  </div>
                  <Card padding={false}>
                    {swapOptions.map((opt, i) => (
                      <button
                        key={opt.id}
                        className="w-full text-left p-4 flex items-center gap-3 transition hover:bg-neutral-800/50"
                        style={{ borderTop: i > 0 ? '0.5px solid var(--border-strong)' : 'none' }}
                      >
                        <Circle className="w-4 h-4 text-neutral-600" />
                        <div className="flex-1">
                          <div className="text-[13px] font-medium">{opt.brand} {opt.model} {opt.size}</div>
                          <div className="text-[11px] text-neutral-500 font-mono mt-0.5">DOM {opt.dom} · {opt.jumps} jumps</div>
                        </div>
                      </button>
                    ))}
                    <button
                      className="w-full text-left p-4 flex items-center gap-3 transition hover:bg-neutral-800/50"
                      style={{ borderTop: swapOptions.length > 0 ? '0.5px solid var(--border-strong)' : 'none' }}
                    >
                      <Plus className="w-4 h-4 text-neutral-400" />
                      <span className="text-[13px] font-medium text-neutral-300">Add new main</span>
                    </button>
                  </Card>
                </div>
              </div>
            ) : (
              <div
                className="rounded-xl p-4 flex items-start gap-3"
                style={{ background: 'rgba(251,191,36,0.05)', border: '0.5px solid rgba(251,191,36,0.2)' }}
              >
                <Info className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: 'var(--status-watch)' }} />
                <div className="text-[12px] leading-relaxed" style={{ color: 'rgba(251,191,36,0.85)' }}>
                  Field-level edits for this component live in Inventory. A future release will add a repack-event flow that
                  records rigger swaps directly from the rig.
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

function Card({ children, padding = true }) {
  return (
    <div
      className={`rounded-xl ${padding ? 'p-5' : ''} overflow-hidden`}
      style={{ background: 'var(--bg)', border: '0.5px solid var(--border-strong)' }}
    >
      {children}
    </div>
  );
}

function Stats({ type, data }) {
  const rows = (() => {
    if (type === 'main') {
      return [
        ['Brand', data.brand],
        ['Model', `${data.model} ${data.size}`],
        ['DOM', data.dom, true],
        ['Jumps', data.jumps, true],
        ['Lineset type', data.lineset.type, true],
        ['Installed', data.lineset.installed, true],
        ['Jumps on lineset', data.lineset.jumps, true],
        ['Status', data.status.toUpperCase(), false, data.status],
      ];
    }
    if (type === 'reserve') {
      return [
        ['Brand', data.brand],
        ['Model', `${data.model} ${data.size}`],
        ['DOM', data.dom, true],
        ['Repacks', `${data.repacks} / ${data.repackLimit}`, true],
        ['Rides', `${data.rides} / ${data.rideLimit}`, true],
        ['Status', data.status.toUpperCase(), false, data.status],
      ];
    }
    if (type === 'aad') {
      return [
        ['Brand', data.brand],
        ['Model', data.model],
        ['Mode', data.mode],
        ['DOM', data.dom, true],
        ['Jumps', data.jumps, true],
        ['Fires', data.fires, true],
      ];
    }
    return [
      ['Brand', data.brand],
      ['Model', data.model],
      ['DOM', data.dom, true],
      ['Jumps', data.jumps, true],
    ];
  })();

  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-2.5">
      {rows.map(([label, value, mono, status]) => {
        const s = status ? STATUS[status] : null;
        return (
          <div key={label} className="flex flex-col">
            <span className="text-[10px] tracking-[0.15em] text-neutral-500 font-medium uppercase">{label}</span>
            <span
              className={`text-[14px] mt-0.5 ${mono ? 'font-mono' : 'font-medium'}`}
              style={{ color: s ? s.text : 'var(--text)' }}
            >
              {value}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function Notes({ notes, expanded, onToggle }) {
  const [draft, setDraft] = useState('');
  return (
    <div className="rounded-xl overflow-hidden" style={{ background: 'var(--bg)', border: '0.5px solid var(--border-strong)' }}>
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between p-4 transition hover:bg-neutral-800/30"
      >
        <div className="flex items-center gap-2.5">
          <MessageSquare className="w-4 h-4 text-neutral-400" />
          <span className="text-[13px] font-medium">Notes</span>
          {notes.length > 0 && (
            <span
              className="text-[10px] font-mono text-neutral-500 px-1.5 py-0.5 rounded-full"
              style={{ background: 'var(--surface-2)' }}
            >
              {notes.length}
            </span>
          )}
        </div>
        <ChevronDown
          className="w-4 h-4 text-neutral-500 transition-transform duration-300"
          style={{ transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)' }}
        />
      </button>
      <div
        className="overflow-hidden transition-all duration-300"
        style={{ maxHeight: expanded ? 500 : 0, opacity: expanded ? 1 : 0 }}
      >
        <div style={{ borderTop: '0.5px solid var(--border-strong)' }}>
          {notes.length === 0 ? (
            <div className="p-5 text-[12px] text-neutral-500 text-center italic">No notes yet.</div>
          ) : (
            <div className="max-h-64 overflow-y-auto">
              {notes.map((n, i) => (
                <div
                  key={i}
                  className="p-4"
                  style={{ borderTop: i > 0 ? '0.5px solid var(--border-strong)' : 'none' }}
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-[12px] font-medium text-neutral-300">{n.author}</span>
                    <span className="text-[10px] text-neutral-500 font-mono">{n.date}</span>
                  </div>
                  <div className="text-[12px] text-neutral-400 leading-relaxed">{n.content}</div>
                </div>
              ))}
            </div>
          )}
          <div className="p-3" style={{ borderTop: '0.5px solid var(--border-strong)', background: 'rgba(0,0,0,0.4)' }}>
            <div
              className="flex items-center gap-2 rounded-lg px-3 py-2"
              style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)' }}
            >
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Add a note…"
                className="flex-1 bg-transparent text-[13px] placeholder:text-neutral-600 text-neutral-100 border-none focus:outline-none"
              />
              <button
                disabled={!draft.trim()}
                onClick={() => setDraft('')}
                className="w-7 h-7 rounded-md flex items-center justify-center transition"
                style={{
                  background: draft.trim() ? 'var(--text)' : 'var(--surface-2)',
                  color: draft.trim() ? 'var(--bg)' : 'var(--text-faint)',
                  cursor: draft.trim() ? 'pointer' : 'not-allowed',
                }}
              >
                <Send className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
