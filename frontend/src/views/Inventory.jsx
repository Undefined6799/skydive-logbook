import React, { useEffect, useState } from 'react';
import { Plus, Search, ChevronRight, Loader2 } from 'lucide-react';
import {
  listRigs,
  listMains,
  listReserves,
  listAads,
  listContainers,
} from '../api';
import { buildInventoryShape } from '../rigShape';
import { StatusDot, PrimaryButton, Card } from '../primitives';
import AddComponentModal from '../modals/AddComponentModal';
import ComponentDetailModal from '../modals/ComponentDetailModal';

const TYPE_LABELS = { container: 'Container', main: 'Main', reserve: 'Reserve', aad: 'AAD' };

export default function Inventory() {
  const [filter, setFilter] = useState('all');
  // Phase 1: real-data wiring. Each list endpoint is fetched once
  // on mount; the rig list is also pulled so we can resolve
  // component.assigned_rig_id → rig.nickname for the ASSIGNMENT
  // column.
  const [all, setAll] = useState(null); // null → loading
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [showAddComponent, setShowAddComponent] = useState(false);
  // Detail modal target. `{ id, type }` while a row is open, null
  // otherwise. The modal fetches its own full record by id+type;
  // the inventory only needs to remember which one was clicked.
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    Promise.all([
      listRigs({ limit: 1000 }),
      listMains({ limit: 1000 }),
      listReserves({ limit: 1000 }),
      listAads({ limit: 1000 }),
      listContainers({ limit: 1000 }),
    ])
      .then(([rigs, mains, reserves, aads, containers]) => {
        if (cancelled) return;
        setAll(buildInventoryShape({ mains, reserves, aads, containers }, rigs));
      })
      .catch((err) => {
        if (!cancelled) setError(err);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  if (error) {
    return (
      <div className="px-10 py-10 max-w-[1100px] text-[13px] text-amber-300">
        Failed to load inventory: {String(error.message || error)}
      </div>
    );
  }
  if (all === null) {
    return (
      <div className="px-10 py-10 max-w-[1100px] flex items-center gap-2 text-[13px] text-neutral-500">
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
        Loading inventory…
      </div>
    );
  }

  const filtered = filter === 'all' ? all : all.filter((c) => c.type === filter);

  const counts = {
    all: all.length,
    main: all.filter((c) => c.type === 'main').length,
    reserve: all.filter((c) => c.type === 'reserve').length,
    aad: all.filter((c) => c.type === 'aad').length,
    container: all.filter((c) => c.type === 'container').length,
  };

  // Phase 1 caveat: the shape adapter doesn't carry a `retired`
  // boolean — components with status='retired' just have
  // `status: 'yellow'` (Phase 2 status math will tighten this).
  // For now, "retired" count is rows where the underlying status
  // is non-active.
  const unassigned = all.filter((c) => !c.assigned).length;
  const retired = 0;  // surfaced after Phase 2 status wiring

  return (
    <div className="px-10 py-10 max-w-[1100px]">
      <div className="flex items-start justify-between gap-4 mb-5">
        <div>
          <div className="text-3xl font-medium tracking-tight">Inventory</div>
          <div className="text-[12px] text-neutral-500 mt-1.5">
            <span className="font-mono text-neutral-400">{all.length}</span> components ·{' '}
            <span className="font-mono text-neutral-400">{unassigned}</span> unassigned ·{' '}
            <span className="font-mono text-neutral-400">{retired}</span> retired
          </div>
        </div>
        <PrimaryButton onClick={() => setShowAddComponent(true)}>
          <Plus className="w-4 h-4" strokeWidth={2.2} />
          Add component
        </PrimaryButton>
      </div>

      <div className="flex items-center gap-2 mb-3.5 flex-wrap">
        <div className="inline-flex gap-0.5 p-0.5 rounded-lg" style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)' }}>
          <FilterBtn active={filter === 'all'} onClick={() => setFilter('all')} count={counts.all}>
            All
          </FilterBtn>
          <FilterBtn active={filter === 'main'} onClick={() => setFilter('main')} count={counts.main}>
            Mains
          </FilterBtn>
          <FilterBtn active={filter === 'reserve'} onClick={() => setFilter('reserve')} count={counts.reserve}>
            Reserves
          </FilterBtn>
          <FilterBtn active={filter === 'aad'} onClick={() => setFilter('aad')} count={counts.aad}>
            AADs
          </FilterBtn>
          <FilterBtn active={filter === 'container'} onClick={() => setFilter('container')} count={counts.container}>
            Containers
          </FilterBtn>
        </div>
        <div
          className="flex-1 min-w-[140px] flex items-center gap-2 rounded-lg px-3 py-1.5"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)' }}
        >
          <Search className="w-3 h-3 text-neutral-500" />
          <input
            placeholder="Search by name or serial…"
            className="flex-1 bg-transparent text-[12px] text-neutral-100 placeholder:text-neutral-500 border-none focus:outline-none"
          />
        </div>
      </div>

      <Card className="overflow-hidden">
        <div
          className="grid items-center px-4 py-2.5 text-[9px] tracking-[0.25em] text-neutral-500 font-medium"
          style={{ gridTemplateColumns: '28px 90px 1fr 76px 130px 24px', gap: 12, borderBottom: '0.5px solid var(--border-strong)' }}
        >
          <div></div>
          <div>TYPE</div>
          <div>COMPONENT</div>
          <div>DOM</div>
          <div>ASSIGNMENT</div>
          <div></div>
        </div>
        {filtered.map((c, i) => (
          <Row
            key={`${c.id}-${i}`}
            c={c}
            last={i === filtered.length - 1}
            onClick={() => c.id && setSelected({ id: c.id, type: c.type })}
          />
        ))}
        {filtered.length === 0 && (
          <div className="text-center text-[12px] text-neutral-500 py-12">
            No components match this filter.
          </div>
        )}
      </Card>

      <AddComponentModal
        visible={showAddComponent}
        onClose={() => setShowAddComponent(false)}
        onCreated={() => setReloadKey((k) => k + 1)}
      />

      <ComponentDetailModal
        componentId={selected?.id}
        componentType={selected?.type}
        onClose={() => setSelected(null)}
        onSaved={() => setReloadKey((k) => k + 1)}
        onDeleted={() => setReloadKey((k) => k + 1)}
      />
    </div>
  );
}

function FilterBtn({ active, onClick, count, children }) {
  return (
    <button
      onClick={onClick}
      className="px-3 py-1.5 rounded-md text-[12px] font-medium transition"
      style={{
        background: active ? 'var(--surface-3)' : 'transparent',
        color: active ? 'var(--text)' : 'var(--text-faint)',
      }}
    >
      {children}
      <span className="font-mono text-neutral-500 ml-1.5">{count}</span>
    </button>
  );
}

// One-line stat summary shown under the brand/model row. Reads from
// the denormalized shape produced by rigShape.js — exposes the data
// most useful when scanning the list (e.g. "is this lineset close to
// retirement?"). Returns an empty string for placeholder components
// where the user hasn't filled anything in yet.
function rowSubtitle(c) {
  if (c.type === 'main') {
    const lj = c.lineset && c.lineset.jumps;
    const jc = c.lineset && c.lineset.jumps_until_critical;
    if (lj != null && jc != null) {
      return `${lj} jumps on lineset · ~${jc} to critical`;
    }
    return c.jumps != null ? `${c.jumps} jumps` : '';
  }
  if (c.type === 'reserve') {
    const parts = [];
    if (c.repacks != null) parts.push(`${c.repacks} repacks`);
    if (c.rides != null && c.rides > 0) parts.push(`${c.rides} rides`);
    return parts.join(' · ');
  }
  if (c.type === 'aad') {
    const parts = [];
    if (c.jumps != null) parts.push(`${c.jumps} jumps`);
    if (c.fires != null) parts.push(`${c.fires} fires`);
    if (c.mode && c.mode !== '—') parts.push(`${c.mode} mode`);
    return parts.join(' · ');
  }
  if (c.type === 'container') {
    return c.jumps != null ? `${c.jumps} jumps` : '';
  }
  return '';
}

function Row({ c, last, onClick }) {
  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if ((e.key === 'Enter' || e.key === ' ') && onClick) {
          e.preventDefault();
          onClick();
        }
      }}
      className="grid items-center px-4 py-2.5 cursor-pointer hover:bg-neutral-800/30 transition focus:outline-none focus:bg-neutral-800/30"
      style={{
        gridTemplateColumns: '28px 90px 1fr 76px 130px 24px',
        gap: 12,
        borderBottom: last ? 'none' : '0.5px solid var(--surface-2)',
        opacity: c.retired ? 0.55 : 1,
      }}
    >
      <StatusDot status={c.status} />
      <span className="text-[12px] text-neutral-500">{TYPE_LABELS[c.type]}</span>
      <div>
        <div>
          <span className="text-[13px] text-neutral-100">
            {c.brand} {c.model}{c.size ? ` ${c.size}` : ''}
          </span>{' '}
          {c.serial && <span className="text-[11px] text-neutral-500 font-mono">SN {c.serial}</span>}
        </div>
        {/* Per-type subtitle surfaces the most actionable stats:
            mains → jumps + projected reline window;
            reserves → repacks + rides;
            AADs → jumps + fires + mode;
            containers → jumps.
            Skipped on unnamed / placeholder components. */}
        {(c.brand && c.brand !== '—') && (
          <div className="text-[11px] text-neutral-500 font-mono mt-0.5">
            {rowSubtitle(c)}
          </div>
        )}
      </div>
      <span className="text-[12px] font-mono text-neutral-400">{c.dom || '—'}</span>
      <div>
        {c.retired ? (
          <span
            className="inline-block text-[10px] tracking-[0.15em] px-2 py-0.5 rounded-full"
            style={{ color: 'var(--status-critical)', background: 'rgba(248,113,113,0.08)', border: '0.5px solid rgba(248,113,113,0.25)' }}
          >
            RETIRED
          </span>
        ) : c.assigned ? (
          <span className="text-[12px] text-neutral-300">{c.assigned}</span>
        ) : (
          <span
            className="inline-block text-[10px] tracking-[0.15em] text-neutral-400 px-2 py-0.5 rounded-full"
            style={{ background: 'var(--surface-2)' }}
          >
            AVAILABLE
          </span>
        )}
      </div>
      <ChevronRight className="w-3 h-3 text-neutral-600" />
    </div>
  );
}
