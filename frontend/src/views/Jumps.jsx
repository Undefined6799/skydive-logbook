import React, { useState, useEffect } from 'react';
import { Plus, Search, ChevronDown, ChevronRight, X, AlertTriangle, RefreshCw } from 'lucide-react';
import { listJumps, listRigs, getMain, getStats, ApiError } from '../api';
import { PrimaryButton, Card } from '../primitives';
import LogJumpModal from '../modals/LogJumpModal';
import JumpDetailModal from '../modals/JumpDetailModal';

// R.2.2-light.d.2: resolve a jump's rig_id to the main canopy
// label shown in the row's sub-line. Two-step lookup against the
// caller-provided maps (loaded once per Jumps view mount). Returns
// null when there's nothing useful to show — JumpRow just renders
// the existing dropzone/aircraft sub-line in that case.
//
// Caveat (mirrors GearCard): the main shown is the rig's CURRENT
// main, NOT the main on the rig at jump time. Future R.2.3 swaps
// this for a frozen-snapshot read.
function resolveMainLabel(jump, rigsById, mainsById) {
  if (!jump.rig_id) return null;
  const rig = rigsById[jump.rig_id];
  if (!rig || !rig.current_main_id) return null;
  const main = mainsById[rig.current_main_id];
  if (!main) return null;
  const parts = [];
  if (main.manufacturer) parts.push(main.manufacturer);
  if (main.model) parts.push(main.model);
  let label = parts.join(' ');
  if (main.size_sqft != null) {
    const sz = String(Number(main.size_sqft));
    label = label ? `${label} — ${sz} sqft` : `${sz} sqft`;
  }
  return label || null;
}


function formatFreefall(totalSeconds) {
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

export default function Jumps() {
  // jumps is the list rendered by JumpsLog. Hoisted up here so a
  // successful create can optimistically prepend the new row before
  // the backend re-fetch returns — matters because mac WKWebView
  // sometimes serves a stale GET from cache after a POST. Optimistic
  // update is the user-visible truth; the re-fetch is confirmation.
  //
  // Sentinel values: null = loading (first fetch in flight),
  // [] = empty, [..] = loaded.
  const [jumps, setJumps] = useState(null);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [showModal, setShowModal] = useState(false);
  // activeJumpId drives JumpDetailModal — null when closed.
  const [activeJumpId, setActiveJumpId] = useState(null);
  // editingJump is the full Jump object being edited. When non-null,
  // LogJumpModal opens in 'edit' mode prefilled from this object.
  const [editingJump, setEditingJump] = useState(null);
  // Career-wide aggregations from GET /api/v1/stats. Fetched on
  // mount and re-fetched whenever reloadKey bumps so create / edit
  // / delete keeps the subtitle's totals honest. The Stats tab itself
  // is on the Profile page now (P.3); only the subtitle reads this
  // here.
  const [stats, setStats] = useState(null);

  // Filter UI state — driven entirely client-side off the loaded list.
  // For 100s–1000s of jumps this is snappier than round-tripping every
  // keystroke; if a future logbook needs server-side filtering, hoist
  // these into ?q=, ?dropzone=, ?discipline= query params on the
  // backend list endpoint.
  const [searchQuery, setSearchQuery] = useState('');
  const [filterDropzone, setFilterDropzone] = useState('');
  const [filterDiscipline, setFilterDiscipline] = useState('');

  // R.2.2-light.d.2: rig → main lookup tables for the JumpsLog row's
  // "main canopy on this jump" display. Resolution path per row:
  //   jump.rig_id → rigsById[id] → mainsById[rig.current_main_id]
  // Populated once per view mount (and re-fetched on reloadKey bump
  // so creating / editing a jump that uses a freshly-added rig
  // refreshes the maps too). Failures are tolerated — rows with
  // unresolvable rig_id just don't show main info, mirroring the
  // GearCard caveat.
  const [rigsById, setRigsById] = useState({});
  const [mainsById, setMainsById] = useState({});

  useEffect(() => {
    let cancelled = false;
    setError(null);
    listJumps({ limit: 100 })
      .then((data) => {
        if (!cancelled) setJumps(data || []);
      })
      .catch((err) => {
        if (!cancelled) setError(err);
      });
    // Stats fetched in parallel — independent failure mode (it's
    // tolerable for the list to render even if stats fail), so the
    // catch swallows so a transient stats error doesn't blank the
    // whole view.
    getStats()
      .then((s) => { if (!cancelled) setStats(s); })
      .catch(() => { /* tolerate */ });
    // R.2.2-light.d.2: load rigs + their current mains for the
    // per-row "main canopy" display. Two-step: list rigs → fan-out
    // to getMain for each unique current_main_id (a typical jumper
    // has 1–3 rigs so this is bounded). Failures swallowed; the
    // list view stays usable in a "no main info" mode.
    listRigs({ limit: 1000 })
      .then((rigs) => {
        if (cancelled) return;
        const byId = {};
        for (const r of rigs) byId[r.id] = r;
        setRigsById(byId);
        // Collect the unique main ids (skipping null) and fetch
        // each. Promise.allSettled so a single 404 (a rig
        // referencing a deleted main) doesn't drop the whole map.
        const mainIds = Array.from(
          new Set(rigs.map((r) => r.current_main_id).filter(Boolean))
        );
        return Promise.allSettled(
          mainIds.map((id) => getMain(id).then((m) => [id, m])),
        );
      })
      .then((results) => {
        if (cancelled || !results) return;
        const byId = {};
        for (const r of results) {
          if (r.status === 'fulfilled') {
            const [id, main] = r.value;
            byId[id] = main;
          }
        }
        setMainsById(byId);
      })
      .catch(() => { /* tolerate — list view stays usable */ });
    return () => { cancelled = true; };
  }, [reloadKey]);

  const suggestedJumpNumber =
    jumps && jumps.length > 0
      ? Math.max(...jumps.map((j) => j.jump_number)) + 1
      : 1;
  const lastDropzone = (jumps && jumps[0]?.dropzone) || '';

  // Build a JumpSummary projection from the full Jump returned by
  // POST. Backend's list endpoint returns this same subset, so the
  // optimistic row blends seamlessly with the rest of the list.
  function summaryFromJump(j) {
    return {
      id: j.id,
      jump_number: j.jump_number,
      title: j.title,
      date: j.date,
      dropzone: j.dropzone,
      // R.2.2-light.d.1: carry the indexable shape forward so the
      // optimistic row in JumpsLog matches the JumpSummary returned
      // by the backend after the confirming re-fetch (otherwise the
      // sub-line would flicker between the two states).
      aircraft: j.aircraft,
      discipline: j.discipline,
      freefall_time_s: j.freefall_time_s,
      rig_id: j.rig_id,
    };
  }

  function handleCreated(created) {
    // 1. Optimistic update: prepend immediately so the user sees the
    //    new jump the moment the modal closes.
    setJumps((prev) => {
      const summary = summaryFromJump(created);
      const list = prev ? [summary, ...prev.filter((j) => j.id !== created.id)] : [summary];
      // Re-sort by date DESC, jump_number DESC to match the backend's
      // ordering (otherwise a back-dated entry would float to the top).
      list.sort((a, b) => {
        if (a.date !== b.date) return a.date < b.date ? 1 : -1;
        return b.jump_number - a.jump_number;
      });
      return list;
    });
    // 2. Trigger a confirming re-fetch. cache: 'no-store' on the GET
    //    means we always hit the backend, so any drift between the
    //    optimistic state and the server's truth gets reconciled.
    setReloadKey((k) => k + 1);
  }

  function handleDeleted(id) {
    // Optimistic remove: drop the row immediately so the close animation
    // doesn't reveal a still-present jump. Re-fetch confirms.
    setJumps((prev) => (prev ? prev.filter((j) => j.id !== id) : prev));
    setReloadKey((k) => k + 1);
  }

  function handleUpdated(updated) {
    // Optimistic merge: replace the row's summary with the updated
    // jump's projection. Re-sort because date or jump_number may have
    // changed and shift the row's position. Re-fetch confirms.
    setJumps((prev) => {
      if (!prev) return prev;
      const list = prev.map((j) =>
        j.id === updated.id ? summaryFromJump(updated) : j
      );
      list.sort((a, b) => {
        if (a.date !== b.date) return a.date < b.date ? 1 : -1;
        return b.jump_number - a.jump_number;
      });
      return list;
    });
    setReloadKey((k) => k + 1);
  }

  return (
    <div className="px-10 py-10 max-w-[1100px]">
      <div className="flex items-start justify-between gap-4 mb-5">
        <div>
          <div className="text-3xl font-medium tracking-tight">Jumps</div>
          <div className="text-[12px] text-neutral-500 mt-1.5">
            {stats ? (
              <>
                <span className="font-mono text-neutral-400">{stats.total}</span> jumps ·{' '}
                <span className="font-mono text-neutral-400">{formatFreefall(stats.freefall_seconds)}</span> of freefall ·{' '}
                <span className="font-mono text-neutral-400">{stats.last_90_days}</span> in the last 90 days
              </>
            ) : (
              <span className="text-neutral-600">Loading career stats…</span>
            )}
          </div>
        </div>
        <PrimaryButton onClick={() => setShowModal(true)}>
          <Plus className="w-4 h-4" strokeWidth={2.2} />
          Log jump
        </PrimaryButton>
      </div>

      {/* Stats tab moved to the Profile page (P.3); the career-stats
          subtitle above is enough context here on the log view. */}
      <JumpsLog
        jumps={jumps}
        error={error}
        onReload={() => setReloadKey((k) => k + 1)}
        onLogJump={() => setShowModal(true)}
        onJumpClick={setActiveJumpId}
        searchQuery={searchQuery}
        setSearchQuery={setSearchQuery}
        filterDropzone={filterDropzone}
        setFilterDropzone={setFilterDropzone}
        filterDiscipline={filterDiscipline}
        setFilterDiscipline={setFilterDiscipline}
        rigsById={rigsById}
        mainsById={mainsById}
      />

      <LogJumpModal
        visible={showModal || editingJump != null}
        mode={editingJump ? 'edit' : 'create'}
        initialJump={editingJump}
        onClose={() => {
          setShowModal(false);
          setEditingJump(null);
        }}
        onCreated={handleCreated}
        onUpdated={handleUpdated}
        suggestedJumpNumber={suggestedJumpNumber}
        lastDropzone={lastDropzone}
      />

      <JumpDetailModal
        jumpId={activeJumpId}
        onClose={() => setActiveJumpId(null)}
        onDeleted={handleDeleted}
        onEdit={(jump) => {
          // Close detail modal, open edit modal prefilled from this jump.
          setActiveJumpId(null);
          setEditingJump(jump);
        }}
      />
    </div>
  );
}

function JumpsLog({
  jumps, error, onReload, onLogJump, onJumpClick,
  searchQuery, setSearchQuery,
  filterDropzone, setFilterDropzone,
  filterDiscipline, setFilterDiscipline,
  rigsById, mainsById,
}) {
  // Compute distinct dropdown options from the loaded list. Sorted
  // alphabetically; counts in parens make it obvious which option
  // narrows the most. Recomputed on every render but the list is
  // small enough (hundreds of rows max) that it's not worth memo'ing.
  const dropzoneOptions = jumps
    ? Array.from(new Set(jumps.map((j) => j.dropzone).filter(Boolean))).sort()
    : [];
  const disciplineOptions = jumps
    ? Array.from(new Set(jumps.map((j) => j.discipline).filter(Boolean))).sort()
    : [];

  // Apply filters client-side. Search is case-insensitive substring
  // match across title + dropzone + aircraft + discipline.
  const filteredJumps = (jumps || []).filter((j) => {
    if (filterDropzone && j.dropzone !== filterDropzone) return false;
    if (filterDiscipline && j.discipline !== filterDiscipline) return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      const haystack = [j.title, j.dropzone, j.aircraft, j.discipline]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });

  const hasActiveFilters = searchQuery || filterDropzone || filterDiscipline;

  return (
    <>
      <FilterBar
        total={jumps?.length || 0}
        matching={filteredJumps.length}
        searchQuery={searchQuery}
        setSearchQuery={setSearchQuery}
        filterDropzone={filterDropzone}
        setFilterDropzone={setFilterDropzone}
        filterDiscipline={filterDiscipline}
        setFilterDiscipline={setFilterDiscipline}
        dropzoneOptions={dropzoneOptions}
        disciplineOptions={disciplineOptions}
        hasActiveFilters={hasActiveFilters}
        onClearAll={() => {
          setSearchQuery('');
          setFilterDropzone('');
          setFilterDiscipline('');
        }}
      />
      {error ? (
        <ErrorBanner error={error} onRetry={onReload} />
      ) : jumps === null ? (
        <LoadingSkeleton />
      ) : jumps.length === 0 ? (
        <EmptyState onLogJump={onLogJump} />
      ) : filteredJumps.length === 0 ? (
        <NoMatchesState onClear={() => {
          setSearchQuery('');
          setFilterDropzone('');
          setFilterDiscipline('');
        }} />
      ) : (
        <Card className="overflow-hidden">
          <div
            className="grid items-center px-4 py-2.5 text-[9px] tracking-[0.25em] text-neutral-500 font-medium"
            style={{ gridTemplateColumns: '56px 70px 1fr 100px 64px 30px', gap: 14, borderBottom: '0.5px solid var(--border-strong)' }}
          >
            <div>JUMP</div>
            <div>DATE</div>
            <div>TITLE · DROPZONE · AIRCRAFT</div>
            <div>DISCIPLINE</div>
            <div className="text-right">FF TIME</div>
            <div></div>
          </div>
          {filteredJumps.map((j, i) => (
            <JumpRow
              key={j.id}
              jump={j}
              last={i === filteredJumps.length - 1}
              onClick={() => onJumpClick(j.id)}
              mainLabel={resolveMainLabel(j, rigsById, mainsById)}
            />
          ))}
        </Card>
      )}
    </>
  );
}

function FilterBar({
  total, matching,
  searchQuery, setSearchQuery,
  filterDropzone, setFilterDropzone,
  filterDiscipline, setFilterDiscipline,
  dropzoneOptions, disciplineOptions,
  hasActiveFilters, onClearAll,
}) {
  return (
    <div className="flex items-center gap-2 mb-4 flex-wrap">
      <div
        className="flex-1 min-w-[200px] flex items-center gap-2 rounded-lg px-3 py-2"
        style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)' }}
      >
        <Search className="w-3.5 h-3.5 text-neutral-500" />
        <input
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search title, dropzone, aircraft, discipline…"
          className="flex-1 bg-transparent text-[12px] text-neutral-100 placeholder:text-neutral-500 border-none focus:outline-none"
        />
        {searchQuery && (
          <button
            onClick={() => setSearchQuery('')}
            className="text-neutral-500 hover:text-neutral-300"
            title="Clear search"
          >
            <X className="w-3 h-3" />
          </button>
        )}
      </div>
      <FilterSelect
        value={filterDropzone}
        onChange={setFilterDropzone}
        options={dropzoneOptions}
        allLabel="All dropzones"
      />
      <FilterSelect
        value={filterDiscipline}
        onChange={setFilterDiscipline}
        options={disciplineOptions}
        allLabel="All disciplines"
      />
      <span className="text-[11px] text-neutral-500 px-1">
        <span className="font-mono text-neutral-400">{matching}</span>
        {matching !== total && (
          <> of <span className="font-mono text-neutral-400">{total}</span></>
        )}
        {' '}matching
      </span>
      {hasActiveFilters && (
        <button
          onClick={onClearAll}
          className="text-[11px] text-neutral-400 hover:text-neutral-200 px-2 py-1 transition"
        >
          Clear filters
        </button>
      )}
    </div>
  );
}

function FilterSelect({ value, onChange, options, allLabel }) {
  return (
    <div
      className="relative inline-flex items-center"
      style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)', borderRadius: 8 }}
    >
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="appearance-none bg-transparent text-[12px] text-neutral-200 pl-3 pr-7 py-2 cursor-pointer focus:outline-none"
        style={{ minWidth: 140 }}
      >
        <option value="" style={{ background: 'var(--surface-1)', color: 'var(--text-muted)' }}>{allLabel}</option>
        {options.map((opt) => (
          <option key={opt} value={opt} style={{ background: 'var(--surface-1)', color: 'var(--text)' }}>
            {opt}
          </option>
        ))}
      </select>
      <ChevronDown className="absolute right-2 w-3 h-3 text-neutral-500 pointer-events-none" />
    </div>
  );
}

function NoMatchesState({ onClear }) {
  return (
    <div
      className="rounded-xl p-8 text-center"
      style={{ background: 'var(--surface-1)', border: '0.5px dashed var(--text-faint)' }}
    >
      <div className="text-[14px] text-neutral-300 font-medium mb-2">No jumps match your filters.</div>
      <div className="text-[12px] text-neutral-500 mb-4">Try clearing them.</div>
      <button
        onClick={onClear}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[12px] text-neutral-200 transition"
        style={{ background: 'transparent', border: '0.5px solid var(--border-strong)' }}
      >
        Clear filters
      </button>
    </div>
  );
}

function JumpRow({ jump, last, onClick, mainLabel }) {
  // JumpSummary shape (v7): id, jump_number, title, date, dropzone,
  // aircraft, discipline, freefall_time_s, rig_id.
  const [year, month, day] = (jump.date || '').split('-');
  const monthShort = month
    ? new Date(`${jump.date}T12:00:00Z`).toLocaleString('en-US', { month: 'short', timeZone: 'UTC' })
    : '';
  // Sub-line builds dynamically from whatever's present so older
  // jumps (no aircraft / no discipline / no rig) just get a shorter
  // line. R.2.2-light.d.2: append the resolved main canopy label
  // when the jump has rig_id and the maps could resolve it.
  const subParts = [jump.dropzone, jump.aircraft, mainLabel].filter(Boolean);
  const ffMinutes = jump.freefall_time_s != null ? Math.floor(jump.freefall_time_s / 60) : null;
  const ffSeconds = jump.freefall_time_s != null ? jump.freefall_time_s % 60 : null;
  const ffDisplay = jump.freefall_time_s != null
    ? `${ffMinutes}:${String(ffSeconds).padStart(2, '0')}`
    : null;
  return (
    <div
      onClick={onClick}
      className="grid items-center px-4 py-3 cursor-pointer hover:bg-neutral-800/30 transition"
      style={{
        gridTemplateColumns: '56px 70px 1fr 100px 64px 30px',
        gap: 14,
        borderBottom: last ? 'none' : '0.5px solid var(--surface-2)',
      }}
    >
      <div className="font-mono text-[13px] text-neutral-400">#{jump.jump_number}</div>
      <div>
        <div className="font-mono text-[12px] text-neutral-300">{monthShort} {day}</div>
        <div className="font-mono text-[10px] text-neutral-500">{year}</div>
      </div>
      <div className="min-w-0">
        <div className="text-[14px] font-medium text-neutral-100 truncate">
          {jump.title || <span className="text-neutral-500 italic">Untitled jump</span>}
        </div>
        <div className="text-[11px] text-neutral-500 mt-0.5 truncate">
          {subParts.length > 0 ? subParts.join(' · ') : <span className="italic">No dropzone</span>}
        </div>
      </div>
      <div>
        {jump.discipline ? (
          <span
            className="inline-block text-[10px] tracking-[0.05em] text-neutral-300 px-2 py-0.5 rounded"
            style={{ background: 'var(--surface-2)' }}
          >
            {jump.discipline}
          </span>
        ) : (
          <span className="text-[10px] text-neutral-600 italic">—</span>
        )}
      </div>
      <div className="text-right font-mono text-[12px] text-neutral-400">
        {ffDisplay || <span className="text-neutral-700">—</span>}
      </div>
      <ChevronRight className="w-3.5 h-3.5 text-neutral-600" />
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <Card className="overflow-hidden">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <div
          key={i}
          className="grid items-center px-4 py-3"
          style={{
            gridTemplateColumns: '56px 70px 1fr 100px 64px 30px',
            gap: 14,
            borderBottom: i === 5 ? 'none' : '0.5px solid var(--surface-2)',
            opacity: 0.5,
          }}
        >
          <div className="h-3 rounded" style={{ background: 'var(--surface-2)', width: 40 }} />
          <div className="h-3 rounded" style={{ background: 'var(--surface-2)', width: 56 }} />
          <div>
            <div className="h-3.5 rounded mb-1" style={{ background: 'var(--surface-2)', width: '60%' }} />
            <div className="h-3 rounded" style={{ background: 'var(--surface-2)', width: '40%' }} />
          </div>
          <div className="h-4 rounded" style={{ background: 'var(--surface-2)', width: 60 }} />
          <div className="h-3 rounded" style={{ background: 'var(--surface-2)', width: 40 }} />
          <div className="h-3 rounded" style={{ background: 'var(--surface-2)', width: 12 }} />
        </div>
      ))}
    </Card>
  );
}

function EmptyState({ onLogJump }) {
  return (
    <div
      className="rounded-xl p-12 text-center"
      style={{ background: 'var(--surface-1)', border: '0.5px dashed var(--text-faint)' }}
    >
      <div className="text-[15px] text-neutral-300 font-medium mb-2">No jumps yet.</div>
      <div className="text-[12px] text-neutral-500 mb-5">Start your logbook by recording your first jump.</div>
      <div className="inline-block">
        <PrimaryButton onClick={onLogJump}>
          <Plus className="w-4 h-4" strokeWidth={2.2} />
          Log first jump
        </PrimaryButton>
      </div>
    </div>
  );
}

function ErrorBanner({ error, onRetry }) {
  // ApiError carries a parsed RFC 9457 problem; everything else is a network error.
  const isApi = error instanceof ApiError;
  const problem = isApi ? error.problem : null;
  const isNetwork = !isApi;

  return (
    <div
      className="rounded-xl p-5 mb-3"
      style={{ background: 'rgba(248,113,113,0.05)', border: '0.5px solid rgba(248,113,113,0.25)' }}
    >
      <div className="flex items-start gap-3 mb-3">
        <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: 'var(--status-critical)' }} />
        <div className="flex-1">
          <div className="text-[13px] font-medium text-neutral-100">
            {isNetwork ? "Couldn't reach the backend" : (problem?.title || 'Request failed')}
          </div>
          <div className="text-[12px] text-neutral-400 mt-1">
            {isNetwork
              ? 'Is uvicorn running on localhost:8000? Try `python -m uvicorn backend.api.rest:app --reload` from the project root.'
              : (problem?.detail || error.message)}
          </div>
          {isApi && problem?.type && (
            <div className="text-[11px] text-neutral-500 mt-2 font-mono">
              type: {problem.type} · status: {problem.status}
              {error.requestId && <> · request: {error.requestId}</>}
            </div>
          )}
        </div>
      </div>
      <button
        onClick={onRetry}
        className="inline-flex items-center gap-1.5 text-[12px] font-medium px-3 py-1.5 rounded-md transition"
        style={{ background: 'transparent', color: 'var(--text)', border: '0.5px solid var(--border-strong)' }}
      >
        <RefreshCw className="w-3 h-3" />
        Retry
      </button>
    </div>
  );
}

// JumpsStats / StatsSkeleton / BarChartCard moved to
// views/CareerStats.jsx (P.3) — the Profile tab renders them now.
// The career-stats subtitle on this page still uses formatFreefall
// below.
