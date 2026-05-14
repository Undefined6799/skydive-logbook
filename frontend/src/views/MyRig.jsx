import React, { useEffect, useState } from 'react';
import { ChevronRight, Plus, Calendar, AlertTriangle, Loader2, Star, Trash2 } from 'lucide-react';
import {
  DndContext,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  closestCenter,
} from '@dnd-kit/core';
import {
  SortableContext,
  horizontalListSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
  arrayMove,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import {
  listRigs,
  listMains,
  listReserves,
  listAads,
  listContainers,
  listJumpers,
  listJumps,
  starRig,
  deleteRig,
  reorderRigs,
} from '../api';
import { buildRigShape } from '../rigShape';
import { StatusDot, ClockPill, StatCard, ProgressRow, SectionLabel, Card, STATUS } from '../primitives';
import ComponentDetailModal from '../modals/ComponentDetailModal';
import AddRigModal from '../modals/AddRigModal';

// Design-system status pill labels (post-2026-05 redesign). Lowercase,
// short, paired with a 6px dot of the same hue. The yellow/red variants
// can be enriched by callers with a short verb-led tail (e.g.
// "watch — plan a reline") when context is available.
const STATUS_LABEL = {
  green: 'ready',
  yellow: 'watch',
  red: 'critical',
};

// Returns a one-line subtitle for the rig header that surfaces the
// worst component-level fact. Falls back to a clean "all components
// nominal" when nothing is amiss.
function rigContextSubtitle(rig) {
  const jurisdiction = rig.jurisdiction === 'both'
    ? 'USPA & CSPA'
    : rig.jurisdiction;
  const lineset = rig.main && rig.main.lineset;
  if (lineset && lineset.status === 'red') {
    return `Sealed under ${jurisdiction} · main lineset below exit weight — do not jump.`;
  }
  if (lineset && lineset.status === 'yellow' && lineset.jumps_until_critical != null) {
    return `Sealed under ${jurisdiction} · main lineset within 50 jumps of critical.`;
  }
  return `Sealed under ${jurisdiction} · all components nominal.`;
}

export default function MyRig() {
  const [activeRigIdx, setActiveRigIdx] = useState(0);
  const [activeComponent, setActiveComponent] = useState(null);
  const [showAddRig, setShowAddRig] = useState(false);
  // Phase 1: real-data wiring. ``rigShapes`` is the array of
  // denormalized rigs the rendering code expects — each built by
  // buildRigShape from the corresponding real Rig + components +
  // jumper. null = loading; [] = empty (no rigs created yet).
  const [rigShapes, setRigShapes] = useState(null);
  const [error, setError] = useState(null);
  // Bumps when AddRigModal completes a successful POST so the
  // effect re-fetches and the new rig shows up in the carousel.
  const [reloadKey, setReloadKey] = useState(0);
  // D58: the rig id currently being starred. While set we disable
  // every card's star button so a double-click can't race two PUTs;
  // a separate pending banner would be overkill for what's typically
  // a sub-100ms write under the writer lock. ``starError`` surfaces
  // a failed PUT so the user knows the move didn't happen.
  const [pendingStarId, setPendingStarId] = useState(null);
  const [starError, setStarError] = useState(null);
  // D59: in-flight reorder PUT. Used to disable drag-and-drop while
  // a save is happening and surface failures inline. The reorder is
  // applied optimistically (rigShapes is rewritten before the
  // server roundtrips); on error we restore the prior order and
  // surface it.
  const [reorderError, setReorderError] = useState(null);
  // Per-rig delete: confirm dialog + in-flight tracking. We render
  // a small confirm modal rather than window.confirm so the UX
  // matches the rest of the app's modal styling.
  const [pendingDeleteId, setPendingDeleteId] = useState(null);  // shape id, or null
  const [deletingId, setDeletingId] = useState(null);
  const [deleteError, setDeleteError] = useState(null);

  async function handleStarRig(rigId) {
    if (pendingStarId) return;  // ignore reentry — single in-flight star
    setPendingStarId(rigId);
    setStarError(null);
    try {
      await starRig(rigId);
      // Server moved the flag atomically; re-fetch so every card
      // reflects the new starred state. The active rig index is
      // intentionally NOT touched — the user starred from whichever
      // card they were viewing, and we don't want to yank focus.
      setReloadKey((k) => k + 1);
    } catch (err) {
      setStarError(err);
    } finally {
      setPendingStarId(null);
    }
  }

  async function handleReorder(oldIndex, newIndex) {
    // Optimistic local reorder for instant feedback; the server
    // call follows. On error we restore the prior order and
    // surface the failure inline. arrayMove is @dnd-kit's helper
    // that returns a new array with the element moved.
    if (oldIndex === newIndex) return;
    setReorderError(null);
    const previous = rigShapes;
    const next = arrayMove(rigShapes, oldIndex, newIndex);
    setRigShapes(next);
    // If the user was viewing the rig that just moved, keep focus
    // on it (its index changed).
    if (activeRigIdx === oldIndex) {
      setActiveRigIdx(newIndex);
    } else if (oldIndex < activeRigIdx && newIndex >= activeRigIdx) {
      setActiveRigIdx(activeRigIdx - 1);
    } else if (oldIndex > activeRigIdx && newIndex <= activeRigIdx) {
      setActiveRigIdx(activeRigIdx + 1);
    }
    try {
      await reorderRigs(next.map((r) => r.id));
      // Server confirmed — no need to reload; the local order
      // already matches what's on disk. We DO want a reload key
      // bump so any future tiebreaker calculation (D58 star auto-
      // move) sees fresh data, but skip it for performance.
    } catch (err) {
      // Roll back the optimistic update and surface.
      setRigShapes(previous);
      setReorderError(err);
    }
  }

  async function handleConfirmDelete() {
    if (!pendingDeleteId) return;
    const id = pendingDeleteId;
    setDeletingId(id);
    setDeleteError(null);
    try {
      await deleteRig(id);
      // Backend cascades component refs back to inventory and may
      // auto-move the star (D58 transition 3). Re-fetch so all
      // cards reflect the new state.
      setReloadKey((k) => k + 1);
      // If the active rig was the one deleted, fall back to index 0.
      // (The reload effect will clamp the index anyway, but
      // setting it here avoids a transient render against a
      // soon-to-be-stale index.)
      setActiveRigIdx(0);
      setPendingDeleteId(null);
    } catch (err) {
      setDeleteError(err);
    } finally {
      setDeletingId(null);
    }
  }

  useEffect(() => {
    let cancelled = false;
    setError(null);
    Promise.all([
      listRigs({ limit: 1000 }),
      listMains({ limit: 1000 }),
      listReserves({ limit: 1000 }),
      listAads({ limit: 1000 }),
      listContainers({ limit: 1000 }),
      listJumpers({ limit: 1000 }),
      // Jumps are optional for the rig view — they only enrich the
      // component jump counters. Catch locally so a slow/failed
      // jumps endpoint doesn't block the rig from rendering.
      listJumps({ limit: 10000 }).catch(() => []),
    ])
      .then(([rigs, mains, reserves, aads, containers, jumpers, jumps]) => {
        if (cancelled) return;
        // v0.1 single-jumper: pick the first jumper for wingloading.
        // When multi-jumper lands, the picker will live on the rig.
        const jumper = jumpers && jumpers.length > 0 ? jumpers[0] : null;
        const lookups = { mains, reserves, aads, containers, jumper, jumps };
        setRigShapes(rigs.map((r) => buildRigShape(r, lookups)));
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
        Failed to load rigs: {String(error.message || error)}
      </div>
    );
  }
  if (rigShapes === null) {
    return (
      <div className="px-10 py-10 max-w-[1100px] flex items-center gap-2 text-[13px] text-neutral-500">
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
        Loading rigs…
      </div>
    );
  }
  if (rigShapes.length === 0) {
    return (
      <div className="px-10 py-10 max-w-[1100px]">
        <div className="text-[22px] font-medium tracking-tight text-neutral-100 mb-3">My rig</div>
        <Card className="p-8 text-center">
          <div className="text-[14px] text-neutral-300 mb-2">No rigs yet.</div>
          <div className="text-[12px] text-neutral-500 mb-5">
            Create components in Inventory, then assemble them into a rig.
          </div>
          <button
            onClick={() => setShowAddRig(true)}
            className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-[12px] font-medium"
            style={{ background: 'var(--surface-3)', color: 'var(--text)' }}
          >
            <Plus className="w-3.5 h-3.5" strokeWidth={2.2} />
            Add rig
          </button>
        </Card>
        <AddRigModal
          visible={showAddRig}
          onClose={() => setShowAddRig(false)}
          onCreated={(rig) => {
            // Refresh data and jump the carousel to the new rig.
            // D59: rigs sort by display_order ASC (first-added is
            // leftmost). On a brand-new logbook this create is
            // the only rig, so index 0 works either way; the
            // empty-state path always lands here.
            setReloadKey((k) => k + 1);
            setActiveRigIdx(0);
          }}
        />
      </div>
    );
  }

  // Clamp the active index so a rig delete on a different page
  // doesn't leave us pointing past the end of the list.
  const safeIdx = Math.min(activeRigIdx, rigShapes.length - 1);
  const rig = rigShapes[safeIdx];

  return (
    <div className="px-10 py-10 max-w-[1100px]">
      <RigCarousel
        rigs={rigShapes}
        activeIdx={safeIdx}
        onSelect={setActiveRigIdx}
        onAddRig={() => setShowAddRig(true)}
        onStar={handleStarRig}
        pendingStarId={pendingStarId}
        onReorder={handleReorder}
        onRequestDelete={(id) => setPendingDeleteId(id)}
        deletingId={deletingId}
      />

      {/* Surface star / reorder / delete failures inline above the
          header so the user sees them without scrolling. Each
          sticks until the next successful op or until the user
          navigates away. */}
      {starError && (
        <ErrorRow
          label="Couldn't update the starred rig"
          err={starError}
        />
      )}
      {reorderError && (
        <ErrorRow
          label="Couldn't save the new order"
          err={reorderError}
        />
      )}
      {deleteError && (
        <ErrorRow
          label="Couldn't delete the rig"
          err={deleteError}
        />
      )}

      <DeleteRigConfirm
        rig={
          pendingDeleteId
            ? rigShapes.find((r) => r.id === pendingDeleteId)
            : null
        }
        deleting={deletingId === pendingDeleteId}
        onCancel={() => {
          if (deletingId) return;  // can't cancel mid-write
          setPendingDeleteId(null);
          setDeleteError(null);
        }}
        onConfirm={handleConfirmDelete}
      />

      <RigHeader rig={rig} />
      <StatsStrip rig={rig} />
      <Components rig={rig} onClick={setActiveComponent} />
      <UpcomingActions actions={rig.actions} />

      {/* Click on a ComponentCard opens the same detail modal that
          Inventory uses, so view/edit/delete are unified. The shape
          adapter exposes the real component id on each per-kind
          shape (rig.main.id etc), so we can pass id+type straight
          through. The rig-side "Change main canopy" swap UX from
          the original mock-data ComponentModal is removed here —
          the proper swap workflow needs its own dedicated modal
          (R.3 / R.5) and shouldn't ride along with view/edit. */}
      <ComponentDetailModal
        componentId={activeComponent ? rig[activeComponent]?.id : null}
        componentType={activeComponent}
        context="rig"
        onClose={() => setActiveComponent(null)}
        onSaved={() => setReloadKey((k) => k + 1)}
        onDeleted={() => setReloadKey((k) => k + 1)}
      />
      <AddRigModal
        visible={showAddRig}
        onClose={() => setShowAddRig(false)}
        onCreated={() => {
          // Without this callback the modal would close on a
          // successful POST but the carousel would keep showing
          // the stale list — the new rig is invisible until a
          // page reload. D59: new rigs land RIGHTMOST (the
          // service stamps display_order=max+1), so we set the
          // active index to the new last position. The reload
          // effect refreshes rigShapes; we use the *post-reload*
          // length, so the index is computed against the current
          // rigShapes length before the new rig appears, plus
          // one for the rig that's about to be added.
          setReloadKey((k) => k + 1);
          setActiveRigIdx(rigShapes ? rigShapes.length : 0);
        }}
      />
    </div>
  );
}

function RigCarousel({
  rigs,
  activeIdx,
  onSelect,
  onAddRig,
  onStar,
  pendingStarId,
  onReorder,
  onRequestDelete,
  deletingId,
}) {
  // D59: drag-and-drop ordering powered by @dnd-kit. PointerSensor
  // requires a small drag distance before activating so that a
  // plain click on the card (to select it) doesn't accidentally
  // count as a drag. KeyboardSensor adds Space-to-pick-up /
  // arrow-keys-to-move accessibility out of the box.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  function handleDragEnd(event) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = rigs.findIndex((r) => r.id === active.id);
    const newIndex = rigs.findIndex((r) => r.id === over.id);
    if (oldIndex < 0 || newIndex < 0) return;
    if (onReorder) onReorder(oldIndex, newIndex);
  }

  return (
    <>
      <div className="flex items-baseline justify-between mb-3">
        <div className="text-[22px] font-medium tracking-tight text-neutral-100">My rig</div>
        <div className="text-[11px] text-neutral-500">
          {rigs.length} {rigs.length === 1 ? 'rig' : 'rigs'} · drag to reorder
        </div>
      </div>
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={handleDragEnd}
      >
        <SortableContext
          items={rigs.map((r) => r.id)}
          strategy={horizontalListSortingStrategy}
        >
          <div className="flex gap-3 overflow-x-auto pb-1 mb-6">
            {rigs.map((r, i) => (
              <SortableRigCard
                key={r.id}
                rig={r}
                index={i}
                active={i === activeIdx}
                onSelect={() => onSelect(i)}
                onStar={onStar}
                onRequestDelete={onRequestDelete}
                starring={pendingStarId === r.id}
                deleting={deletingId === r.id}
              />
            ))}
            <button
              onClick={onAddRig}
              className="flex-shrink-0 rounded-xl flex items-center justify-center cursor-pointer transition"
              style={{
                background: 'transparent',
                border: '1px dashed var(--border-strong)',
                color: 'var(--text-muted)',
                minWidth: 180,
                padding: '14px 20px',
                gap: 8,
              }}
              onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-muted)'; }}
            >
              <Plus className="w-3.5 h-3.5" strokeWidth={1.8} />
              <span className="text-[14px] font-medium">Add rig</span>
            </button>
          </div>
        </SortableContext>
      </DndContext>
    </>
  );
}


// Mockup `.rig-tab` — clean card with just name + mono meta, and a
// sky-blue ★ marker pinned top-right when the rig is starred. Active
// state uses an accent border + surface-2 background. Trash icon
// fades in only on hover so the default state stays uncluttered.
function SortableRigCard({
  rig: r,
  active,
  onSelect,
  onStar,
  onRequestDelete,
  starring,
  deleting,
}) {
  const isStarred = Boolean(r.starred);
  const {
    attributes, listeners, setNodeRef,
    transform, transition, isDragging,
  } = useSortable({ id: r.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    background: active ? 'var(--surface-2)' : 'var(--surface-1)',
    border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
    borderRadius: 12,
    padding: '14px 20px',
    minWidth: 180,
    opacity: isDragging ? 0.55 : deleting ? 0.4 : 1,
    cursor: isDragging ? 'grabbing' : 'grab',
    zIndex: isDragging ? 10 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className="flex-shrink-0 text-left relative transition flex flex-col group"
      onClick={(e) => {
        if (deleting) return;
        if (e.defaultPrevented) return;
        if (onSelect) onSelect();
      }}
      {...attributes}
      {...listeners}
    >
      {/* Star marker — sky-blue ★ pinned top-right when starred,
          empty outline shown on hover so the user can star a
          non-default rig. Clicking on a non-starred star sets this
          rig as the default; clicking an already-starred star is
          a no-op (the server treats explicit unstar as forbidden). */}
      <span
        role="button"
        aria-label={isStarred ? `Starred — ${r.name} is your default rig` : `Star ${r.name} as your default rig`}
        aria-pressed={isStarred}
        aria-disabled={starring || undefined}
        tabIndex={starring ? -1 : 0}
        onClick={(e) => {
          e.stopPropagation();
          if (starring) return;
          if (!isStarred && onStar) onStar(r.id);
        }}
        onKeyDown={(e) => {
          if (e.key !== 'Enter' && e.key !== ' ') return;
          e.stopPropagation();
          e.preventDefault();
          if (starring) return;
          if (!isStarred && onStar) onStar(r.id);
        }}
        onPointerDown={(e) => e.stopPropagation()}
        className="absolute"
        style={{
          top: 14, right: 16,
          cursor: starring ? 'progress' : isStarred ? 'default' : 'pointer',
          opacity: starring ? 0.55 : isStarred ? 1 : 0,
          transition: 'opacity 120ms ease-out',
        }}
      >
        <Star
          className="w-3.5 h-3.5"
          strokeWidth={isStarred ? 0 : 1.6}
          style={{
            color: 'var(--accent)',
            fill: isStarred ? 'var(--accent)' : 'none',
          }}
        />
      </span>
      {!isStarred && (
        <style>{`
          .group:hover [aria-label*="Star ${r.name}"] { opacity: 1 !important; }
        `}</style>
      )}

      {/* Trash — hidden by default, fades in on hover. Click opens the
          confirm dialog in the parent. */}
      <span
        role="button"
        aria-label={`Delete ${r.name}`}
        tabIndex={deleting ? -1 : 0}
        onClick={(e) => {
          e.stopPropagation();
          if (deleting) return;
          if (onRequestDelete) onRequestDelete(r.id);
        }}
        onKeyDown={(e) => {
          if (e.key !== 'Enter' && e.key !== ' ') return;
          e.stopPropagation();
          e.preventDefault();
          if (deleting) return;
          if (onRequestDelete) onRequestDelete(r.id);
        }}
        onPointerDown={(e) => e.stopPropagation()}
        className="absolute"
        style={{
          top: 14, left: 16,
          cursor: deleting ? 'progress' : 'pointer',
          opacity: deleting ? 1 : 0,
          transition: 'opacity 120ms ease-out, color 120ms ease-out',
          color: 'var(--text-faint)',
        }}
      >
        {deleting ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" style={{ color: 'var(--text-muted)' }} />
        ) : (
          <Trash2 className="w-3.5 h-3.5" strokeWidth={1.6} />
        )}
      </span>
      <style>{`
        .group:hover [aria-label="Delete ${r.name}"] { opacity: 1 !important; color: var(--status-critical) !important; }
      `}</style>

      <span style={{ fontWeight: 600, fontSize: 15, color: 'var(--text)' }}>{r.name}</span>
      <span
        className="font-mono"
        style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}
      >
        {[r.main.model, r.main.size, r.container.model].filter(Boolean).join(' · ')}
      </span>
    </div>
  );
}


function ErrorRow({ label, err }) {
  return (
    <div
      className="mb-3 px-3 py-2 rounded-md text-[12px] flex items-start gap-2"
      style={{ background: 'rgba(248,113,113,0.06)', border: '0.5px solid #3a1f1f', color: 'var(--status-critical)' }}
      role="alert"
    >
      <AlertTriangle className="w-3.5 h-3.5 mt-0.5" />
      <div className="flex-1 min-w-0">
        {label}: {String(err.message || err)}
      </div>
    </div>
  );
}


function DeleteRigConfirm({ rig, deleting, onCancel, onConfirm }) {
  // Confirm dialog for the destructive delete action. The backend
  // soft-deletes (D19) so recovery is possible via .trash/, but
  // the carousel hides trashed rigs and the components get their
  // assigned_rig_id cleared (D37 cascade) so this still surprises
  // a user who clicked × by accident.
  if (!rig) return null;
  return (
    <>
      <div
        onClick={deleting ? undefined : onCancel}
        className="fixed inset-0 z-40"
        style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)' }}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-rig-confirm-title"
        className="fixed inset-0 z-50 flex items-start justify-center p-6 pointer-events-none"
      >
        <div
          onClick={(e) => e.stopPropagation()}
          className="rounded-2xl w-full max-w-md overflow-hidden flex flex-col pointer-events-auto mt-24"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)' }}
        >
          <div className="px-5 pt-5 pb-3" style={{ borderBottom: '0.5px solid var(--border-strong)' }}>
            <div className="text-[9px] tracking-[0.25em] text-neutral-500 font-medium mb-1">DELETE RIG</div>
            <div id="delete-rig-confirm-title" className="text-[17px] font-medium tracking-tight">
              Delete &ldquo;{rig.name}&rdquo;?
            </div>
          </div>
          <div className="px-5 py-4 text-[12px] text-neutral-400 leading-relaxed">
            The rig is moved to the trash folder on disk (recoverable
            from the filesystem), and its main, reserve, AAD, and
            container return to inventory as unassigned. Any jumps
            already logged against this rig keep their pinned
            rig-snapshot, so historical wear math is unchanged.
            {rig.starred && (
              <div
                className="mt-2.5 px-2.5 py-1.5 rounded-md text-[11px]"
                style={{ background: 'var(--accent-soft)', border: '0.5px solid var(--accent-soft-border)', color: 'var(--accent)' }}
              >
                This is your starred (default) rig. The star will
                automatically move to whichever remaining rig you
                most recently jumped — or the leftmost remaining
                rig if none has jumps yet.
              </div>
            )}
          </div>
          <div
            className="flex items-center gap-2 px-5 py-3"
            style={{ background: 'var(--surface-1)', borderTop: '0.5px solid var(--border-strong)' }}
          >
            <div className="flex-1" />
            <button
              type="button"
              onClick={onCancel}
              disabled={deleting}
              className="px-3 py-1.5 text-[12px] text-neutral-400 transition hover:text-neutral-200 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onConfirm}
              disabled={deleting}
              className="px-3.5 py-1.5 rounded-md text-[12px] font-medium transition flex items-center gap-1.5 disabled:opacity-60"
              style={{
                background: 'var(--status-critical-bg)',
                color: 'var(--status-critical)',
                border: '0.5px solid var(--status-critical)',
              }}
            >
              {deleting && <Loader2 className="w-3 h-3 animate-spin" />}
              Delete rig
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

// Single status-block card matching reviews/design-system/redesign-rig.html.
// Combines the status pill row, title, subtitle, and the 4-column
// headline-metrics strip — separated by hairline vertical rules —
// into one card that lives above the components grid.
function RigHeader({ rig }) {
  const s = STATUS[rig.status];
  // Lineset-aware label.
  let label = STATUS_LABEL[rig.status];
  const lineset = rig.main && rig.main.lineset;
  if (rig.status === 'yellow' && lineset && lineset.status === 'yellow') {
    label = `${label} — plan a reline`;
  } else if (rig.status === 'red' && lineset && lineset.status === 'red') {
    label = `${label} — do not jump`;
  }
  return (
    <div
      className="rounded-xl mb-4 p-7"
      style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border)' }}
    >
      <div className="flex items-center gap-2 mb-3">
        <span
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium"
          style={{ background: s.bg, color: s.text }}
        >
          <span
            aria-hidden="true"
            style={{
              display: 'inline-block', width: 6, height: 6,
              borderRadius: 999, background: s.dot,
            }}
          />
          {label}
        </span>
        {rig.starred && (
          <span
            className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-medium"
            style={{
              background: 'var(--accent-soft)',
              border: '0.5px solid var(--accent-soft-border)',
              color: 'var(--accent)',
            }}
            aria-label="This rig is your default for logging jumps"
          >
            <Star className="w-2.5 h-2.5" strokeWidth={0} fill="var(--accent)" />
            default
          </span>
        )}
      </div>
      <h1 className="text-3xl font-medium tracking-tight">{rig.name}</h1>
      <div className="text-[13px] text-neutral-500 mt-1">
        {rigContextSubtitle(rig)}
      </div>

      <div
        className="grid grid-cols-4 mt-6 pt-6"
        style={{ borderTop: '0.5px solid var(--border)' }}
      >
        <HeadlineMetric
          label="WINGLOAD"
          value={rig.wingloading > 0 ? rig.wingloading.toFixed(2) : '—'}
          unit="lb/sqft"
          foot={
            rig.wingloading > 0 && rig.main && rig.main.size
              ? `${Math.round(rig.wingloading * rig.main.size)} lb / ${rig.main.size} sqft`
              : ''
          }
          isFirst
        />
        <HeadlineMetric
          label="AAD MODE"
          value={rig.aad.mode || '—'}
          foot={rig.aad.brand && rig.aad.model ? `${rig.aad.brand} ${rig.aad.model}` : ''}
        />
        <HeadlineMetric
          label="MAIN JUMPS"
          value={rig.main.jumps}
          foot="on canopy"
        />
        <HeadlineMetric
          label="RESERVE DUE"
          value={rig.uspaDays != null ? rig.uspaDays : '—'}
          unit={rig.uspaDays != null ? 'days' : ''}
          foot={
            rig.cspaDays != null
              ? `USPA · ${rig.cspaDays} d CSPA`
              : rig.uspaDays != null ? 'USPA' : 'not sealed'
          }
          isLast
        />
      </div>
    </div>
  );
}

// One column inside the headline-metrics strip. Mono number with
// a dim unit + small footer line. Hairline vertical rule between
// columns; first/last skip the rule on the outer side.
function HeadlineMetric({ label, value, unit, foot, isFirst, isLast }) {
  return (
    <div
      style={{
        paddingLeft: isFirst ? 0 : 24,
        paddingRight: isLast ? 0 : 24,
        borderRight: isLast ? 'none' : '0.5px solid var(--border)',
      }}
    >
      <div
        className="text-[10px] font-medium mb-2"
        style={{ color: 'var(--text-muted)', letterSpacing: '0.08em', textTransform: 'uppercase' }}
      >
        {label}
      </div>
      <div className="font-mono text-[22px] font-medium" style={{ color: 'var(--text)', letterSpacing: '-0.01em' }}>
        {value}
        {unit && (
          <span className="ml-1 text-[12px] font-normal" style={{ color: 'var(--text-muted)' }}>
            {unit}
          </span>
        )}
      </div>
      {foot && (
        <div className="text-[11px] font-mono mt-1" style={{ color: 'var(--text-faint)' }}>
          {foot}
        </div>
      )}
    </div>
  );
}

// Legacy StatsStrip becomes a no-op — its content moved into RigHeader.
function StatsStrip() {
  return null;
}

// Section header pattern from reviews/design-system/redesign-rig.html:
// title left, faint mono aside right, generous top margin.
function SectionHead({ title, aside }) {
  return (
    <div
      className="flex items-baseline justify-between"
      style={{ marginTop: 48, marginBottom: 16 }}
    >
      <h3
        className="text-[13px] font-medium m-0"
        style={{ color: 'var(--text-muted)', letterSpacing: '0.08em', textTransform: 'uppercase' }}
      >
        {title}
      </h3>
      {aside && (
        <span className="text-[12px] font-mono" style={{ color: 'var(--text-faint)' }}>
          {aside}
        </span>
      )}
    </div>
  );
}

function Components({ rig, onClick }) {
  return (
    <div>
      <SectionHead title="Components" aside="tap a card to view detail or edit" />
      <div className="grid grid-cols-2 gap-4">
        <ComponentCard type="main" data={rig.main} onClick={() => onClick('main')} />
        <ComponentCard
          type="reserve"
          data={rig.reserve}
          rig={rig}
          onClick={() => onClick('reserve')}
        />
        <ComponentCard type="aad" data={rig.aad} onClick={() => onClick('aad')} />
        <ComponentCard type="container" data={rig.container} onClick={() => onClick('container')} />
      </div>
    </div>
  );
}

function ComponentCard({ type, data, onClick, rig }) {
  const s = STATUS[data.status];
  // Lowercase type labels per the design-system mockup (`comp-type`).
  const TYPE_LABEL = { main: 'main canopy', reserve: 'reserve', aad: 'aad', container: 'container' };
  // Meta line: mono, "DOM 03/2026 · 99 sqft" for canopies; "CPI mode"
  // for AADs; "DOM 03/2023" for containers.
  const meta =
    type === 'aad' ? `${data.mode || '—'} mode`
    : type === 'main' || type === 'reserve'
      ? `DOM ${data.dom || '—'}${data.size ? ` · ${data.size} sqft` : ''}`
      : `DOM ${data.dom || '—'}`;
  return (
    <button
      onClick={onClick}
      className="text-left rounded-xl transition group relative"
      style={{
        background: 'var(--surface-1)',
        border: '1px solid var(--border)',
        padding: '22px 24px',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--border-strong)'; }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)'; }}
    >
      {/* Chevron pinned to the top-right per the mockup's
          `.component-card .chevron` rule. */}
      <ChevronRight
        className="w-3.5 h-3.5 absolute"
        style={{ top: 18, right: 22, color: 'var(--text-faint)' }}
      />
      <div className="flex items-center gap-2 mb-3">
        {/* Status pill (not just a dot). Pastel fill, lowercase label,
            6px hue dot baked into ::before in the design-system pill. */}
        <span
          className="inline-flex items-center gap-1.5 rounded-full font-medium"
          style={{
            background: s.bg, color: s.text,
            padding: '2px 8px', fontSize: 10,
          }}
        >
          <span
            aria-hidden="true"
            style={{
              display: 'inline-block', width: 5, height: 5,
              borderRadius: 999, background: s.dot,
            }}
          />
          {STATUS_LABEL[data.status] || 'ready'}
        </span>
        <span
          className="font-medium"
          style={{
            fontSize: 10, color: 'var(--text-muted)',
            letterSpacing: '0.1em', textTransform: 'uppercase',
          }}
        >
          {TYPE_LABEL[type]}
        </span>
      </div>
      <h4
        className="font-semibold m-0 truncate"
        style={{ fontSize: 17, color: 'var(--text)', marginBottom: 2 }}
      >
        {data.brand} {data.model}{data.size ? ` — ${data.size} sqft` : ''}
      </h4>
      <div
        className="font-mono"
        style={{ fontSize: 12, color: 'var(--text-faint)', marginBottom: 16 }}
      >
        {meta}
      </div>

      {type === 'main' && (
        <div>
          <KVRow label="Jumps on canopy" value={data.jumps} />
          <KVRow label="Jumps on lineset" value={data.lineset.jumps} />
          {/* `.comp-wear` block per the mockup — hairline top, label
              left + status-colored value right, slim bar, mono helper
              line below with residual / breaking strength / wear rate. */}
          <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
            <div className="flex justify-between items-baseline" style={{ marginBottom: 8 }}>
              <span
                className="font-medium"
                style={{
                  fontSize: 11, color: 'var(--text-muted)',
                  letterSpacing: '0.08em', textTransform: 'uppercase',
                }}
              >
                Lineset
              </span>
              <span
                className="font-mono font-medium"
                style={{
                  fontSize: 13,
                  color:
                    data.lineset.status === 'red' ? 'var(--status-critical)'
                    : data.lineset.status === 'yellow' ? 'var(--status-watch)'
                    : 'var(--status-ready)',
                }}
              >
                {data.lineset.status === 'red'
                  ? 'below exit weight'
                  : data.lineset.jumps_until_critical != null
                    ? `~${data.lineset.jumps_until_critical} jumps to critical`
                    : `${data.lineset.remaining}% strength`}
              </span>
            </div>
            <div
              className="rounded-sm overflow-hidden"
              style={{ height: 4, background: 'var(--surface-3)' }}
            >
              <div
                className="rounded-sm"
                style={{
                  height: '100%',
                  width: `${data.lineset.remaining}%`,
                  background:
                    data.lineset.status === 'red' ? 'var(--status-critical)'
                    : data.lineset.status === 'yellow' ? 'var(--status-watch)'
                    : 'var(--status-ready)',
                }}
              />
            </div>
            <div
              className="font-mono"
              style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 6 }}
            >
              {data.lineset.residual_lb} of {data.lineset.breaking_strength_lb} lb · {data.lineset.remaining}% strength · wear ~1.0 lb/jump
            </div>
          </div>
        </div>
      )}

      {type === 'reserve' && (
        <div className="flex flex-col gap-3">
          <ProgressStat
            label="Repacks"
            value={data.repacks}
            max={data.repackLimit}
          />
          <ProgressStat
            label="Rides"
            value={data.rides}
            max={data.rideLimit}
          />
          {rig && rig.uspaDays != null && (() => {
            const tone = rig.uspaDays < 30 ? 'critical'
              : rig.uspaDays < 60 ? 'watch'
              : 'ready';
            const color = `var(--status-${tone})`;
            return (
              <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
                <div className="flex justify-between items-baseline" style={{ marginBottom: 8 }}>
                  <span
                    className="font-medium"
                    style={{
                      fontSize: 11, color: 'var(--text-muted)',
                      letterSpacing: '0.08em', textTransform: 'uppercase',
                    }}
                  >
                    USPA repack window
                  </span>
                  <span className="font-mono font-medium" style={{ fontSize: 13, color }}>
                    {rig.uspaDays} days
                  </span>
                </div>
                <div
                  className="rounded-sm overflow-hidden"
                  style={{ height: 4, background: 'var(--surface-3)' }}
                >
                  <div
                    className="rounded-sm"
                    style={{
                      height: '100%',
                      width: `${Math.max(0, Math.min(100, (rig.uspaDays / 180) * 100))}%`,
                      background: color,
                    }}
                  />
                </div>
              </div>
            );
          })()}
        </div>
      )}

      {type === 'aad' && (
        <div>
          <KVRow label="Jumps" value={data.jumps} />
          <KVRow label="Fires" value={data.fires} />
          <KVRow
            label="Next action"
            value={data.daysToAction != null ? `${data.daysToAction}d` : 'none scheduled'}
            mono={data.daysToAction != null}
            muted={data.daysToAction == null}
          />
        </div>
      )}

      {type === 'container' && (
        <div>
          <KVRow label="Jumps" value={data.jumps} />
          <KVRow
            label="Notes"
            value={(data.notes?.length || 0) > 0 ? data.notes.length : '—'}
            muted={!data.notes || data.notes.length === 0}
          />
        </div>
      )}
    </button>
  );
}

// Bar variant of a `.comp-stat` row. Label + ratio value on top,
// slim accent-tinted progress bar underneath. Used on the reserve
// card for repacks/rides so the user sees how much of the lifetime
// budget is consumed at a glance. Color thresholds match the rest
// of the wear bars: > 90% red, > 75% cream, otherwise sage.
function ProgressStat({ label, value, max }) {
  const safeMax = max && max > 0 ? max : 1;
  const pct = Math.max(0, Math.min(100, (value / safeMax) * 100));
  const color =
    pct > 90 ? 'var(--status-critical)'
    : pct > 75 ? 'var(--status-watch)'
    : 'var(--status-ready)';
  return (
    <div>
      <div className="flex justify-between items-baseline" style={{ marginBottom: 6 }}>
        <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{label}</span>
        <span className="font-mono" style={{ fontSize: 13, color: 'var(--text)' }}>
          {value} / {max}
        </span>
      </div>
      <div
        className="rounded-sm overflow-hidden"
        style={{ height: 4, background: 'var(--surface-3)' }}
      >
        <div
          className="rounded-sm"
          style={{ height: '100%', width: `${pct}%`, background: color }}
        />
      </div>
    </div>
  );
}


// Matches the design-system `.comp-stat` row from
// reviews/design-system/redesign-rig.html — label left, value right,
// 13px, 6px vertical padding, mono on the value.
function KVRow({ label, value, mono = true, muted }) {
  return (
    <div className="grid items-baseline" style={{ gridTemplateColumns: '1fr auto', gap: 12, padding: '6px 0' }}>
      <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{label}</span>
      <span
        className={mono ? 'font-mono' : ''}
        style={{ fontSize: 13, color: muted ? 'var(--text-muted)' : 'var(--text)' }}
      >
        {value}
      </span>
    </div>
  );
}

function UpcomingActions({ actions }) {
  if (!actions || actions.length === 0) return null;
  return (
    <div>
      <SectionHead title="Upcoming actions" aside="sorted by due date" />
      {/* Mockup pattern: single card with hairline rows; dot + title +
          meta on one line; subtle hover background. No tall pill rail. */}
      <div
        className="rounded-xl overflow-hidden"
        style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}
      >
        {actions.map((a, i) => {
          const dotColor =
            a.level === 'critical' ? 'var(--status-critical)'
            : a.level === 'warning' ? 'var(--status-watch)'
            : 'var(--status-ready)';
          const meta = a.detail
            ? a.detail
            : a.days != null
              ? `in ${a.days} days`
              : '';
          return (
            <div
              key={i}
              className="grid items-center transition cursor-pointer"
              style={{
                gridTemplateColumns: 'auto 1fr auto',
                gap: 16,
                padding: '16px 24px',
                borderTop: i > 0 ? '1px solid var(--border)' : 'none',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--surface-2)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
            >
              <span
                aria-hidden="true"
                style={{
                  display: 'inline-block', width: 8, height: 8,
                  borderRadius: 999, background: dotColor,
                }}
              />
              <span style={{ fontSize: 14, color: 'var(--text)' }}>{a.text}</span>
              <span className="font-mono" style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                {meta}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
