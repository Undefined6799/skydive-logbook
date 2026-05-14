import React, { useEffect, useState, useCallback, useRef } from 'react';
import { Plus, Loader2, AlertTriangle, MapPin, Pencil, Trash2, Plane, Star, MoreHorizontal } from 'lucide-react';
import {
  listDropzones,
  getDropzone,
  listJumps,
  deleteDropzone as apiDeleteDropzone,
  starDropzone,
  ApiError,
} from '../api';
import DropzoneModal from '../modals/DropzoneModal';
import { PrimaryButton } from '../primitives';

// Same labels + delta values as DropzoneModal — keep them in sync
// (small enough that a const here is simpler than importing).
const ENV_META = {
  clean_grass: { label: 'Clean grass', delta: '+0.00 lb' },
  dust_sand_salt: { label: 'Dust / sand / salt', delta: '+0.20 lb' },
  desert: { label: 'Desert', delta: '+0.25 lb' },
};

// ISO-3166-1 alpha-2 → display name. Storage stays the ISO code per
// the XSD; this is only for rendering in the DZ card subtitle so
// "CA" doesn't read as "California" to a US user.
const COUNTRY_NAMES = {
  CA: 'Canada',
  US: 'United States',
  FR: 'France',
  DE: 'Germany',
  ES: 'Spain',
  PT: 'Portugal',
  IT: 'Italy',
  GB: 'United Kingdom',
  AU: 'Australia',
  NZ: 'New Zealand',
  MX: 'Mexico',
  BR: 'Brazil',
  AR: 'Argentina',
  CL: 'Chile',
  ZA: 'South Africa',
  CH: 'Switzerland',
  AT: 'Austria',
  NL: 'Netherlands',
  BE: 'Belgium',
  IE: 'Ireland',
  SE: 'Sweden',
  NO: 'Norway',
  FI: 'Finland',
  DK: 'Denmark',
  CZ: 'Czechia',
  PL: 'Poland',
  JP: 'Japan',
  TH: 'Thailand',
  AE: 'United Arab Emirates',
};

function countryDisplay(iso) {
  if (!iso) return '';
  return COUNTRY_NAMES[iso.toUpperCase()] || iso;
}

export default function Dropzones() {
  const [dzs, setDzs] = useState([]);
  // Per-DZ jump counts. Map of dz.id → number. Computed once on
  // refresh by listing every jump and bucketing by dropzone_id.
  const [jumpsByDz, setJumpsByDz] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Modal state. ``mode`` is 'create' | 'edit'; ``editing`` carries
  // the full Dropzone record when mode === 'edit' (need the record
  // because the summary list omits province/notes/timestamps).
  const [modal, setModal] = useState({ visible: false, mode: 'create', editing: null });

  // Two-click delete confirmation. ``pendingDeleteId`` is the id
  // currently armed; clicking trash on a different row resets the
  // arm. Cleared on success or when the modal opens for another op.
  const [pendingDeleteId, setPendingDeleteId] = useState(null);
  const [deleting, setDeleting] = useState(false);

  // D60: the dropzone id currently being starred. While set we
  // disable other star buttons so a double-click can't fire two
  // PUTs and end up with the server seeing a transient "two
  // starred" state (which the writer lock would still serialize
  // safely, but pessimistic UI is cheaper than reasoning about it).
  const [pendingStarId, setPendingStarId] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const summaries = await listDropzones({ limit: 1000 });
      // Hydrate each row to the full record so the page can render
      // the fleet (R.D.6). The DZ picker on LogJumpModal still uses
      // summaries — it doesn't need the aircraft list. Bounded N
      // (~100 DZs for any working jumper), parallel fetch.
      const full = await Promise.all(
        summaries.map((s) => getDropzone(s.id).catch(() => null)),
      );
      setDzs(full.filter((d) => d !== null));
      // Fetch jumps in the background so each card can render the
      // "N jumps logged" stat. Decoupled from the DZ load so a slow
      // or stubbed-out /api/v1/jumps (e.g. in tests) doesn't block
      // the dropzone list from appearing.
      listJumps({ limit: 10000 })
        .then((jumps) => {
          const buckets = {};
          for (const j of jumps || []) {
            if (j.dropzone_id) {
              buckets[j.dropzone_id] = (buckets[j.dropzone_id] || 0) + 1;
            }
          }
          setJumpsByDz(buckets);
        })
        .catch(() => { /* stat is optional; ignore failures */ });
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  function handleAdd() {
    setPendingDeleteId(null);
    setModal({ visible: true, mode: 'create', editing: null });
  }

  function handleEdit(dz) {
    // ``dz`` is already the full record (the page now holds full
    // records, R.D.6). No extra round-trip needed.
    setPendingDeleteId(null);
    setModal({ visible: true, mode: 'edit', editing: dz });
  }

  async function handleStar(id) {
    if (pendingStarId) return;  // single in-flight star at a time
    // No-op when the target is already starred — D60 forbids
    // explicit unstar, and the server treats it as idempotent, so
    // we can skip the round-trip too. The DZCard also gates clicks
    // when isStarred, but a defensive guard here is cheap.
    const target = dzs.find((d) => d.id === id);
    if (!target || target.starred) return;
    setPendingStarId(id);
    setError(null);
    try {
      const updated = await starDropzone(id);
      // D60 invariant: exactly one DZ is starred while ≥1 exists.
      // The server cleared every prior starred DZ atomically under
      // the writer lock; mirror that in local state so the card
      // grid re-renders immediately. Cheaper than re-fetching the
      // full list (which would also re-fetch the aircraft fleet
      // per DZ).
      setDzs((prev) =>
        prev.map((d) =>
          d.id === id
            ? { ...d, ...updated, starred: true }
            : d.starred
              ? { ...d, starred: false }
              : d,
        ),
      );
    } catch (err) {
      setError(err);
    } finally {
      setPendingStarId(null);
    }
  }

  async function handleDelete(id) {
    if (pendingDeleteId !== id) {
      setPendingDeleteId(id);
      return;
    }
    setDeleting(true);
    try {
      await apiDeleteDropzone(id);
      // Optimistic: drop the row from local state. A refresh would
      // also work but adds a network round-trip for no UX gain.
      setDzs((prev) => prev.filter((d) => d.id !== id));
      setPendingDeleteId(null);
    } catch (err) {
      setError(err);
    } finally {
      setDeleting(false);
    }
  }

  function handleCreated(created) {
    // ``created`` is the full Dropzone from POST. Insert it
    // directly so the page state stays a list of full records.
    setDzs((prev) => sortByName([...prev, created]));
  }

  function handleUpdated(updated) {
    setDzs((prev) =>
      sortByName(prev.map((d) => (d.id === updated.id ? updated : d))),
    );
  }

  const countries = new Set(dzs.map((d) => d.country)).size;
  const totalJumps = Object.values(jumpsByDz).reduce((a, b) => a + b, 0);

  return (
    <div className="px-10 py-10 max-w-[1100px]">
      <div className="flex items-start justify-between gap-4 mb-5">
        <div>
          <div className="text-3xl font-medium tracking-tight">Dropzones</div>
          <div className="text-[12px] text-neutral-500 mt-1.5">
            <span className="font-mono text-neutral-400">{dzs.length}</span> place{dzs.length === 1 ? '' : 's'} ·{' '}
            <span className="font-mono text-neutral-400">{countries}</span> {countries === 1 ? 'country' : 'countries'}
            {' · '}
            <span className="font-mono text-neutral-400">{totalJumps}</span> jump{totalJumps === 1 ? '' : 's'} total
          </div>
        </div>
        <PrimaryButton onClick={handleAdd}>
          <Plus className="w-4 h-4" strokeWidth={2.2} />
          Add dropzone
        </PrimaryButton>
      </div>

      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}

      {loading && dzs.length === 0 && (
        <div className="flex items-center gap-2 text-[12px] text-neutral-500 py-8">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          Loading dropzones…
        </div>
      )}

      {!loading && dzs.length === 0 && !error && <EmptyState onAdd={handleAdd} />}

      {dzs.length > 0 && (
        <div className="grid grid-cols-2 gap-2.5 mt-3">
          {dzs.map((d) => (
            <DZCard
              key={d.id}
              dz={d}
              jumpsLogged={jumpsByDz[d.id] || 0}
              onEdit={() => handleEdit(d)}
              onDelete={() => handleDelete(d.id)}
              onStar={() => handleStar(d.id)}
              starring={pendingStarId === d.id}
              starringAny={pendingStarId !== null}
              deleteArmed={pendingDeleteId === d.id}
              deleting={deleting && pendingDeleteId === d.id}
            />
          ))}
        </div>
      )}

      <DropzoneModal
        visible={modal.visible}
        mode={modal.mode}
        initialDropzone={modal.editing}
        onClose={() => setModal({ visible: false, mode: 'create', editing: null })}
        onCreated={handleCreated}
        onUpdated={handleUpdated}
      />
    </div>
  );
}

function sortByName(list) {
  return [...list].sort((a, b) => {
    const an = a.name.toLowerCase();
    const bn = b.name.toLowerCase();
    if (an !== bn) return an < bn ? -1 : 1;
    const ac = a.city.toLowerCase();
    const bc = b.city.toLowerCase();
    return ac < bc ? -1 : ac > bc ? 1 : 0;
  });
}

function EmptyState({ onAdd }) {
  return (
    <div
      className="rounded-2xl p-10 mt-3 text-center"
      style={{ background: 'var(--surface-1)', border: '0.5px dashed var(--border)' }}
    >
      <MapPin className="w-5 h-5 text-neutral-500 mx-auto mb-3" strokeWidth={1.5} />
      <div className="text-[15px] text-neutral-200 font-medium">No dropzones yet</div>
      <div className="text-[12px] text-neutral-500 mt-1.5 max-w-md mx-auto">
        Add a dropzone to record where you've jumped. The environment field
        feeds lineset-wear projections for each main canopy.
      </div>
      <button
        onClick={onAdd}
        className="mt-4 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[12px] font-medium"
        style={{ background: 'var(--text)', color: 'var(--bg)' }}
      >
        <Plus className="w-3.5 h-3.5" strokeWidth={2.2} />
        Add your first dropzone
      </button>
    </div>
  );
}

function DZCard({
  dz,
  jumpsLogged = 0,
  onEdit,
  onDelete,
  onStar,
  starring,
  starringAny,
  deleteArmed,
  deleting,
}) {
  const env = ENV_META[dz.environment] || {
    label: dz.environment,
    delta: '?',
  };
  const fleet = dz.aircraft || [];
  // D60: the starred DZ wears a filled amber star; unstarred DZs
  // show a hollow neutral star whose click sets this DZ as the
  // new default. No explicit unstar — clicking the already-starred
  // DZ is a no-op (cursor stays default, click handler bails).
  const isStarred = Boolean(dz.starred);
  return (
    <div
      className="rounded-xl p-4 transition group"
      style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)' }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            {/* Inline star marker shown ONLY for the default DZ —
                matches the design-system mockup which omits the empty
                star on non-default cards. The star button in the
                action row below is still the entry point for setting
                a new default. */}
            {isStarred && (
              <Star
                className="w-3 h-3 flex-shrink-0"
                strokeWidth={0}
                style={{ color: 'var(--accent)', fill: 'var(--accent)' }}
                aria-hidden="true"
              />
            )}
            <div className="text-[15px] font-medium text-neutral-100 truncate">{dz.name}</div>
          </div>
          <div className="text-[11px] text-neutral-500 mt-1 flex items-center gap-1.5">
            <MapPin className="w-3 h-3 text-neutral-600" strokeWidth={1.6} />
            {dz.city}{dz.province ? `, ${dz.province}` : ''}, {countryDisplay(dz.country)}
          </div>
        </div>
      </div>

      {fleet.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2.5">
          {fleet.map((p, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] text-neutral-300"
              style={{ background: 'var(--surface-2)' }}
              title={p.tail_number ? `${p.model} · ${p.tail_number}` : p.model}
            >
              <Plane className="w-2.5 h-2.5 text-neutral-500" strokeWidth={1.6} />
              {p.model}
              {p.tail_number && (
                <span className="text-neutral-500 font-mono">· {p.tail_number}</span>
              )}
            </span>
          ))}
        </div>
      )}

      <div
        className="flex items-center justify-between mt-3.5 pt-2.5"
        style={{ borderTop: '0.5px solid var(--border-strong)' }}
      >
        <div className="flex flex-col gap-0.5">
          <span className="text-[11px] text-neutral-300">{env.label}</span>
          <span className="text-[10px] font-mono text-neutral-500">
            {env.delta} / jump
            {' · '}
            <span className="text-neutral-300">{jumpsLogged}</span>
            {jumpsLogged === 1 ? ' jump logged' : ' jumps logged'}
          </span>
        </div>
        <OverflowMenu
          isStarred={isStarred}
          starring={starring}
          starringAny={starringAny}
          deleteArmed={deleteArmed}
          deleting={deleting}
          dzName={dz.name}
          onStar={onStar}
          onEdit={onEdit}
          onDelete={onDelete}
        />
      </div>
    </div>
  );
}

function ErrorBanner({ error, onDismiss }) {
  const isApi = error instanceof ApiError;
  const problem = isApi ? error.problem : null;
  return (
    <div
      className="p-3 rounded-xl flex items-start gap-3 mb-3"
      style={{
        background: 'rgba(248,113,113,0.05)',
        border: '0.5px solid rgba(248,113,113,0.25)',
      }}
    >
      <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" style={{ color: 'var(--status-critical)' }} />
      <div className="flex-1 min-w-0">
        <div className="text-[12px] font-medium text-neutral-100">
          {isApi ? (problem?.title || 'Request failed') : 'Something went wrong'}
        </div>
        {problem?.detail && (
          <div className="text-[11px] text-neutral-500 mt-0.5">{problem.detail}</div>
        )}
        {!isApi && (
          <div className="text-[11px] text-neutral-500 mt-0.5">{error.message}</div>
        )}
      </div>
      <button
        onClick={onDismiss}
        className="text-[11px] text-neutral-500 hover:text-neutral-300"
      >
        Dismiss
      </button>
    </div>
  );
}

// ⋯ overflow menu on each DZ card. Click toggles a small popover
// with "Set as default", "Edit", "Delete" entries. Click-outside or
// Escape dismisses. Matches reviews/design-system/redesign-dropzones.html.
function OverflowMenu({
  isStarred, starring, starringAny, deleteArmed, deleting,
  dzName, onStar, onEdit, onDelete,
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) { if (e.key === 'Escape') setOpen(false); }
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={deleting}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Actions for ${dzName}`}
        className="w-7 h-7 rounded transition flex items-center justify-center"
        style={{
          background: open ? 'var(--surface-2)' : 'transparent',
          color: 'var(--text-faint)',
        }}
        onMouseEnter={(e) => { if (!open) e.currentTarget.style.color = 'var(--text)'; }}
        onMouseLeave={(e) => { if (!open) e.currentTarget.style.color = 'var(--text-faint)'; }}
      >
        <MoreHorizontal className="w-4 h-4" strokeWidth={1.8} />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-8 z-20 rounded-lg overflow-hidden"
          style={{
            background: 'var(--surface-2)',
            border: '0.5px solid var(--border-strong)',
            boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
            minWidth: 160,
          }}
        >
          <button
            type="button"
            role="menuitem"
            disabled={isStarred || starring || starringAny}
            onClick={() => {
              setOpen(false);
              if (!isStarred && onStar) onStar();
            }}
            className="w-full text-left px-3 py-2 text-[13px] flex items-center gap-2 transition disabled:opacity-50"
            style={{ color: isStarred ? 'var(--accent)' : 'var(--text)' }}
            onMouseEnter={(e) => {
              if (e.currentTarget.disabled) return;
              e.currentTarget.style.background = 'var(--surface-3)';
            }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
          >
            <Star
              className="w-3.5 h-3.5"
              strokeWidth={isStarred ? 0 : 1.6}
              style={{
                color: isStarred ? 'var(--accent)' : 'var(--text-faint)',
                fill: isStarred ? 'var(--accent)' : 'none',
              }}
            />
            {isStarred ? 'Default' : 'Set as default'}
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => { setOpen(false); if (onEdit) onEdit(); }}
            className="w-full text-left px-3 py-2 text-[13px] flex items-center gap-2 transition"
            style={{ color: 'var(--text)' }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--surface-3)'; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
          >
            <Pencil className="w-3.5 h-3.5" strokeWidth={1.6} style={{ color: 'var(--text-faint)' }} />
            Edit
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => { setOpen(false); if (onDelete) onDelete(); }}
            className="w-full text-left px-3 py-2 text-[13px] flex items-center gap-2 transition"
            style={{
              color: 'var(--status-critical)',
              borderTop: '0.5px solid var(--border)',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--status-critical-bg)'; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
          >
            <Trash2 className="w-3.5 h-3.5" strokeWidth={1.6} />
            {deleteArmed ? 'Confirm delete' : 'Delete'}
          </button>
        </div>
      )}
    </div>
  );
}
