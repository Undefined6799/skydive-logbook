import React, { useEffect, useState } from 'react';
import {
  X, Trash2, AlertTriangle, Loader2, Pencil, Save, Package,
  ArrowRightLeft, Check, Scissors,
} from 'lucide-react';
import {
  getMain, updateMain, deleteMain, listMains, swapMain,
  getReserve, updateReserve, deleteReserve,
  getAad, updateAad, deleteAad,
  getContainer, updateContainer, deleteContainer,
  ApiError,
} from '../api';
import { LINE_MATERIALS, composeLineType, decomposeLineType } from '../lineTypes';
import RelineModal from './RelineModal';

// Phase 1.i — view + edit + delete an inventory component.
//
// Mirrors JumpDetailModal's pattern: opens read-only, "Edit" toggles
// to a form, "Save" PUTs the full record back. PUT is full-replace
// (D7), so on save we preserve every field the form doesn't expose
// (notes_log, lineset_history, recert_extensions, current_lineset
// for mains, …) by spreading the original record under the user's
// edits. assigned_rig_id is forbidden on the *Update models per
// R.2.0c.iii.b — backend strips/echoes it server-side, so we just
// don't send it.
//
// "Currently on rig" is purely informational here. Status edits to
// non-active for assigned components are rejected with 409 by the
// backend; we surface that error in the banner. Delete also goes
// through unconditionally — Phase 2 will add a "detach first"
// guard if D37 grows one.

const TYPE_LABELS = {
  main: 'MAIN CANOPY',
  reserve: 'RESERVE',
  aad: 'AAD',
  container: 'CONTAINER',
};

const FETCHERS = {
  main: getMain,
  reserve: getReserve,
  aad: getAad,
  container: getContainer,
};

const UPDATERS = {
  main: updateMain,
  reserve: updateReserve,
  aad: updateAad,
  container: updateContainer,
};

const DELETERS = {
  main: deleteMain,
  reserve: deleteReserve,
  aad: deleteAad,
  container: deleteContainer,
};


export default function ComponentDetailModal({
  componentId, componentType, onClose, onSaved, onDeleted,
  // 'inventory' (default) — opened from the Inventory list. The
  // "Change main canopy" swap UI is hidden here because swap is a
  // rig-side operation; if the user wants to swap a main, they
  // open the rig that holds it from MyRig.
  // 'rig' — opened from MyRig. Swap UI is shown for mains assigned
  // to the rig.
  context = 'inventory',
}) {
  const [record, setRecord] = useState(null);  // full backend record
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // S.4 — main-swap picker. Opens an inline list of unassigned
  // mains; selection POSTs /rigs/{rig}/swap_main, closes the
  // modal, and triggers a reload upstream so the rig view picks
  // up the new main. ``null`` = picker closed; ``[]`` = open and
  // loading; an array = open with items.
  const [swapOptions, setSwapOptions] = useState(null);
  const [swapLoading, setSwapLoading] = useState(false);
  const [swapping, setSwapping] = useState(false);
  // R.1.b — Reline modal toggle. When the user clicks the Reline
  // button we mount the dedicated modal on top of this one (its
  // z-index sits above ours). On successful submit it calls
  // onRelined, which we wire to refresh the on-disk record so the
  // CURRENT LINESET card immediately reflects the new install.
  const [relining, setRelining] = useState(false);

  // Lock body scroll while open + reset transient state when the
  // (id, type) pair changes.
  useEffect(() => {
    if (!componentId || !componentType) {
      document.body.style.overflow = '';
      setRecord(null);
      setForm(null);
      setEditing(false);
      setError(null);
      setConfirmingDelete(false);
      setSwapOptions(null);
      setRelining(false);
      return;
    }
    document.body.style.overflow = 'hidden';
    let cancelled = false;
    setLoading(true);
    setError(null);
    setEditing(false);
    setConfirmingDelete(false);
    setSwapOptions(null);
    const fetcher = FETCHERS[componentType];
    fetcher(componentId)
      .then((data) => {
        if (cancelled) return;
        setRecord(data);
        setForm(formFromRecord(componentType, data));
      })
      .catch((err) => { if (!cancelled) setError(err); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => {
      cancelled = true;
      document.body.style.overflow = '';
    };
  }, [componentId, componentType]);

  if (!componentId || !componentType) return null;

  function update(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function handleSave() {
    if (!record || !form) return;
    setSaving(true);
    setError(null);
    try {
      const payload = buildUpdatePayload(componentType, record, form);
      const updated = await UPDATERS[componentType](componentId, payload);
      setRecord(updated);
      setForm(formFromRecord(componentType, updated));
      setEditing(false);
      if (onSaved) onSaved(updated);
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      await DELETERS[componentType](componentId);
      if (onDeleted) onDeleted(componentId);
      onClose();
    } catch (err) {
      setError(err);
      setConfirmingDelete(false);
    } finally {
      setDeleting(false);
    }
  }

  function handleCancelEdit() {
    setForm(formFromRecord(componentType, record));
    setEditing(false);
    setError(null);
  }

  // Swap-main flow. Open the picker → fetch all mains, filter to
  // those that are unassigned (or already on this rig as a no-op
  // self-pick). Tap one → POST swap_main → close modal + reload.
  async function handleOpenSwap() {
    setSwapOptions([]);  // open in loading state
    setSwapLoading(true);
    setError(null);
    try {
      const all = await listMains({ limit: 1000 });
      const candidates = (all || []).filter((m) => {
        if (m.id === record?.id) return false;            // hide current
        if (m.status !== 'active') return false;          // D37: active only
        // Unassigned, or already on this rig (the idempotent self
        // case). Hide mains on OTHER rigs — backend would 409.
        return m.assigned_rig_id == null
            || m.assigned_rig_id === record?.assigned_rig_id;
      });
      setSwapOptions(candidates);
    } catch (err) {
      setError(err);
      setSwapOptions(null);
    } finally {
      setSwapLoading(false);
    }
  }

  function handleCancelSwap() {
    setSwapOptions(null);
  }

  async function handlePickSwap(newMainId) {
    if (!record?.assigned_rig_id) return;
    setSwapping(true);
    setError(null);
    try {
      await swapMain(record.assigned_rig_id, newMainId);
      if (onSaved) onSaved();
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setSwapping(false);
    }
  }

  return (
    <>
      <div
        onClick={(saving || deleting) ? undefined : onClose}
        className="fixed inset-0 z-40"
        style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)' }}
      />
      <div className="fixed inset-0 z-50 flex items-start justify-center p-6 pointer-events-none overflow-y-auto">
        <div
          onClick={(e) => e.stopPropagation()}
          className="rounded-2xl w-full max-w-2xl pointer-events-auto mt-10 mb-10 flex flex-col"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)', maxHeight: 'calc(100vh - 80px)' }}
        >
          <Header
            type={componentType}
            record={record}
            loading={loading}
            editing={editing}
            onClose={onClose}
            disabled={saving || deleting}
          />
          <div className="overflow-y-auto flex-1 p-5 space-y-3">
            {error && <ErrorBanner error={error} />}
            {loading && !record && <LoadingSkeleton />}
            {record && !editing && (
              <ViewBody
                type={componentType}
                record={record}
                context={context}
                swapOptions={swapOptions}
                swapLoading={swapLoading}
                swapping={swapping}
                onOpenSwap={handleOpenSwap}
                onCancelSwap={handleCancelSwap}
                onPickSwap={handlePickSwap}
                onOpenReline={() => setRelining(true)}
              />
            )}
            {record && editing && form && (
              <EditBody
                type={componentType}
                form={form}
                onChange={update}
                disabled={saving}
              />
            )}
          </div>
          {record && (
            <Footer
              record={record}
              editing={editing}
              saving={saving}
              deleting={deleting}
              confirmingDelete={confirmingDelete}
              onEdit={() => { setEditing(true); setError(null); }}
              onCancel={editing ? handleCancelEdit : (confirmingDelete ? () => setConfirmingDelete(false) : onClose)}
              onSave={handleSave}
              onDelete={handleDelete}
            />
          )}
        </div>
      </div>

      {/* Reline modal stacks on top via z-[60]/[70]; on success
          we pull a fresh record so the CURRENT LINESET card and
          the rigShape wear math both reflect the new install. */}
      {relining && componentType === 'main' && record && (
        <RelineModal
          main={record}
          onClose={() => setRelining(false)}
          onRelined={async () => {
            setRelining(false);
            // Refetch the main so the modal body reflects the
            // archived old lineset + the freshly-installed new
            // one. Errors here are tolerated — the parent's
            // onSaved trigger does its own reload.
            try {
              const fresh = await getMain(record.id);
              setRecord(fresh);
              setForm(formFromRecord('main', fresh));
            } catch { /* tolerate */ }
            if (onSaved) onSaved();
          }}
        />
      )}
    </>
  );
}


function Header({ type, record, loading, editing, onClose, disabled }) {
  const title = (() => {
    if (loading || !record) return <span className="text-neutral-500">Loading…</span>;
    if (type === 'aad') return record.model || 'unknown';
    const parts = [record.model || 'unknown'];
    if (record.size_sqft != null) parts.push(`${Number(record.size_sqft)} sqft`);
    if (type === 'container' && record.size) parts.push(record.size);
    return parts.join(' · ');
  })();
  return (
    <div
      className="flex items-start justify-between px-6 pt-5 pb-4"
      style={{ borderBottom: '0.5px solid var(--border-strong)' }}
    >
      <div className="min-w-0 flex-1 pr-4">
        <div className="flex items-center gap-2 mb-1.5">
          <Package className="w-3 h-3 text-neutral-500" />
          <span className="text-[9px] tracking-[0.25em] text-neutral-500 font-medium">
            {TYPE_LABELS[type]}
          </span>
          {editing && (
            <span
              className="inline-block text-[9px] tracking-[0.15em] px-2 py-0.5 rounded-full"
              style={{ color: 'var(--status-watch)', background: 'rgba(251,191,36,0.08)', border: '0.5px solid rgba(251,191,36,0.25)' }}
            >
              EDITING
            </span>
          )}
        </div>
        <div className="text-[20px] font-medium tracking-tight truncate">{title}</div>
        {record && (
          <div className="text-[12px] text-neutral-500 mt-0.5">
            {record.manufacturer || <span className="italic">unknown manufacturer</span>}
            {record.serial && <> · <span className="font-mono">SN {record.serial}</span></>}
          </div>
        )}
      </div>
      <button
        onClick={onClose}
        disabled={disabled}
        className="w-8 h-8 rounded-lg flex items-center justify-center transition hover:bg-neutral-800 flex-shrink-0 disabled:opacity-40"
        style={{ background: 'var(--surface-2)' }}
      >
        <X className="w-3.5 h-3.5 text-neutral-400" />
      </button>
    </div>
  );
}


// View-mode body — renders a kind-aware KV grid plus an assignment
// badge when the component is currently on a rig (per the on-disk
// assigned_rig_id field, which the backend echoes through GET).
//
// For mains assigned to a rig, surfaces a "Change main canopy"
// affordance that opens the swap picker (S.4). The picker filters
// to active + unassigned mains client-side, then POSTs to
// /rigs/{rig_id}/swap_main on selection.
function ViewBody({
  type, record, context,
  swapOptions, swapLoading, swapping,
  onOpenSwap, onCancelSwap, onPickSwap,
  onOpenReline,
}) {
  // Swap is a rig-side operation. Suppressed in the inventory
  // context — if the user wants to swap, they navigate to the rig
  // that holds the main from MyRig and open it from there.
  const canSwap = type === 'main'
    && record.assigned_rig_id
    && context === 'rig';
  // Reline is a canopy-level operation: applies whether the main
  // is currently on a rig or sitting in inventory. R.1.b.
  const canReline = type === 'main';
  const pickerOpen = swapOptions !== null;
  return (
    <>
      {record.assigned_rig_id && (
        <div
          className="rounded-xl p-3 flex items-start gap-2 text-[12px]"
          style={{ background: 'var(--status-ready-bg)', border: '0.5px solid var(--status-ready)', color: 'var(--status-ready)' }}
        >
          <Package className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
          <div>
            Currently installed on a rig.
            Remove it from the rig before changing status.
          </div>
        </div>
      )}

      {/* Reline lives inside the CURRENT LINESET card below — its
          natural home, since it's the action that operates on that
          card's content. The top-of-view action row only carries
          the rig-side swap when applicable. */}
      {canSwap && !pickerOpen && (
        <button
          onClick={onOpenSwap}
          className="w-full rounded-xl py-2.5 text-[12px] font-medium flex items-center justify-center gap-2 transition"
          style={{
            background: 'transparent',
            color: 'var(--text)',
            border: '0.5px solid var(--border-strong)',
          }}
        >
          <ArrowRightLeft className="w-3.5 h-3.5" />
          Change main canopy
        </button>
      )}

      {canSwap && pickerOpen && (
        <SwapPicker
          options={swapOptions}
          loading={swapLoading}
          swapping={swapping}
          onCancel={onCancelSwap}
          onPick={onPickSwap}
        />
      )}
      <Card>
        <SectionLabel>IDENTIFICATION</SectionLabel>
        <KVGrid>
          <KV label="Manufacturer">{record.manufacturer || <Empty />}</KV>
          <KV label="Model">{record.model || <Empty />}</KV>
          <KV label="Serial">
            {record.serial ? <span className="font-mono">{record.serial}</span> : <Empty />}
          </KV>
          <KV label="Date of manufacture">
            {record.date_of_manufacture
              ? <span className="font-mono">{record.date_of_manufacture}</span>
              : <Empty />}
          </KV>
          <KV label="Status">
            <StatusBadge status={record.status} />
          </KV>
        </KVGrid>
      </Card>

      {(type === 'main' || type === 'reserve') && (
        <Card>
          <SectionLabel>{type === 'main' ? 'GEOMETRY & COUNTERS' : 'GEOMETRY, COUNTERS & LIMITS'}</SectionLabel>
          <KVGrid>
            <KV label="Size">
              {record.size_sqft != null
                ? <span className="font-mono">{Number(record.size_sqft)} sqft</span>
                : <Empty />}
            </KV>
            {type === 'main' && (
              <KV label="Jumps (initial)">
                <span className="font-mono">{record.jump_count_initial ?? 0}</span>
              </KV>
            )}
            {type === 'main' && (
              /* D45: surface RDS in view mode so the wear-math
                 contribution is discoverable without entering edit. */
              <KV label="RDS">
                {record.has_rds
                  ? <span style={{ color: 'var(--status-ready)' }}>Yes · +0.15 lb/jump</span>
                  : <span className="text-neutral-500">No</span>}
              </KV>
            )}
            {type === 'reserve' && (
              <>
                <KV label="Repacks (initial)">
                  <span className="font-mono">{record.repack_count_initial ?? 0}</span>
                </KV>
                <KV label="Rides (initial)">
                  <span className="font-mono">{record.ride_count_initial ?? 0}</span>
                </KV>
                <KV label="Repack limit">
                  {record.repack_limit != null
                    ? <span className="font-mono">{record.repack_limit}</span>
                    : <Empty />}
                </KV>
                <KV label="Ride limit">
                  {record.ride_limit != null
                    ? <span className="font-mono">{record.ride_limit}</span>
                    : <Empty />}
                </KV>
              </>
            )}
          </KVGrid>
        </Card>
      )}

      {type === 'main' && (
        <Card>
          <div className="flex items-center justify-between mb-2.5 gap-2">
            <SectionLabel>CURRENT LINESET</SectionLabel>
            {canReline && (
              <button
                onClick={onOpenReline}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-medium transition hover:bg-neutral-800/40"
                style={{
                  background: 'transparent',
                  color: 'var(--text)',
                  border: '0.5px solid var(--border-strong)',
                }}
                title="Install a fresh lineset; archive the current one to history"
              >
                <Scissors className="w-3 h-3" />
                Reline
              </button>
            )}
          </div>
          {record.current_lineset ? (
            <>
              <KVGrid>
                <KV label="Line type">{record.current_lineset.line_type}</KV>
                <KV label="Breaking strength">
                  <span className="font-mono">{record.current_lineset.breaking_strength_lb} lb</span>
                </KV>
                <KV label="Install date">
                  <span className="font-mono">{record.current_lineset.install_date}</span>
                </KV>
                <KV label="Installed by">
                  {record.current_lineset.installed_by || <Empty />}
                </KV>
                <KV label="Jumps on lineset">
                  <span className="font-mono">{record.current_lineset.jumps_on_lineset_initial ?? 0}</span>
                </KV>
                <div />
              </KVGrid>
              <div className="text-[11px] text-neutral-500 mt-2.5 leading-relaxed">
                Per D46, exit weight is read live from your jumper profile.
                Reline archives the current lineset to history with its id
                preserved (D36) so historical jumps stay pinned.
              </div>
            </>
          ) : (
            <div className="text-[12px] text-neutral-500 italic">
              Not yet lined — click <span className="text-neutral-400">Reline</span> to record the first install.
            </div>
          )}
        </Card>
      )}

      {type === 'aad' && (
        <Card>
          <SectionLabel>AAD MODE & COUNTERS</SectionLabel>
          <KVGrid>
            <KV label="Mode">{record.mode || <Empty />}</KV>
            <KV label="Mode changeable">
              {record.is_changeable_mode === true ? 'yes'
                : record.is_changeable_mode === false ? 'no'
                : <Empty />}
            </KV>
            <KV label="Jumps (initial)">
              <span className="font-mono">{record.jump_count_initial ?? 0}</span>
            </KV>
            <KV label="Fires (initial)">
              <span className="font-mono">{record.fire_count_initial ?? 0}</span>
            </KV>
          </KVGrid>
        </Card>
      )}

      {type === 'container' && (
        <Card>
          <SectionLabel>GEOMETRY & COUNTERS</SectionLabel>
          <KVGrid>
            <KV label="Size">
              {record.size ? <span className="font-mono">{record.size}</span> : <Empty />}
            </KV>
            <KV label="Jumps (initial)">
              <span className="font-mono">{record.jump_count_initial ?? 0}</span>
            </KV>
          </KVGrid>
        </Card>
      )}

      <Card>
        <SectionLabel>FORENSICS</SectionLabel>
        <div className="grid grid-cols-[110px_1fr] gap-y-1.5 text-[12px]">
          <span className="text-neutral-500">ID</span>
          <span className="font-mono text-neutral-300 truncate" title={record.id}>{record.id}</span>
          {record.created_at && (
            <>
              <span className="text-neutral-500">Created at</span>
              <span className="font-mono text-neutral-400">{record.created_at}</span>
            </>
          )}
          <span className="text-neutral-500">Edited at</span>
          {record.updated_at && record.updated_at !== record.created_at ? (
            <span className="font-mono text-neutral-400">{record.updated_at}</span>
          ) : (
            <span className="text-neutral-600 italic">never edited</span>
          )}
        </div>
      </Card>
    </>
  );
}


// Edit-mode body — kind-aware form. Field set is the user-facing
// subset of the *Update model: identification + status + per-kind
// counters/geometry, plus current_lineset for mains. The reline
// workflow (move current_lineset → history, install fresh) is
// still R.5 territory; in-place edits here preserve the lineset
// id (D36) so historical jumps that pin to it stay valid.
function EditBody({ type, form, onChange, disabled }) {
  return (
    <>
      <Card>
        <SectionLabel>IDENTIFICATION</SectionLabel>
        <FormGrid>
          <Field label="MANUFACTURER">
            <input
              value={form.manufacturer}
              onChange={(e) => onChange('manufacturer', e.target.value)}
              disabled={disabled}
              className={inputCls}
            />
          </Field>
          <Field label="MODEL">
            <input
              value={form.model}
              onChange={(e) => onChange('model', e.target.value)}
              disabled={disabled}
              className={inputCls}
            />
          </Field>
        </FormGrid>
        <FormGrid>
          <Field label="SERIAL">
            <input
              value={form.serial}
              onChange={(e) => onChange('serial', e.target.value)}
              disabled={disabled}
              className={inputCls}
            />
          </Field>
          <Field label="DATE OF MANUFACTURE">
            <input
              type="date"
              value={form.date_of_manufacture}
              onChange={(e) => onChange('date_of_manufacture', e.target.value)}
              disabled={disabled}
              className={inputCls}
            />
          </Field>
        </FormGrid>
        <FormGrid>
          <Field label="STATUS">
            <select
              value={form.status}
              onChange={(e) => onChange('status', e.target.value)}
              disabled={disabled}
              className={inputCls}
            >
              <option value="active">active</option>
              <option value="retired">retired</option>
              <option value="lost">lost</option>
            </select>
          </Field>
          <div />
        </FormGrid>
      </Card>

      {(type === 'main' || type === 'reserve') && (
        <Card>
          <SectionLabel>{type === 'main' ? 'GEOMETRY & COUNTERS' : 'GEOMETRY, COUNTERS & LIMITS'}</SectionLabel>
          <FormGrid>
            <Field label="SIZE (sqft)">
              <input
                type="number"
                step="0.1"
                min="0"
                value={form.size_sqft}
                onChange={(e) => onChange('size_sqft', e.target.value)}
                disabled={disabled}
                className={inputCls}
              />
            </Field>
            {type === 'main' && (
              <Field label="JUMPS (INITIAL)">
                <input
                  type="number"
                  min="0"
                  value={form.jump_count_initial}
                  onChange={(e) => onChange('jump_count_initial', e.target.value)}
                  disabled={disabled}
                  className={inputCls}
                />
              </Field>
            )}
          </FormGrid>
          {type === 'main' && (
            /* D45: RDS flag. Toggleable in edit mode so a jumper who
               installed an RDS mod on an existing canopy can flip it
               without re-creating the inventory record. Read-only
               when ``disabled`` (view mode); the parent toggles edit
               state via the same path as every other field. */
            <label className="flex items-start gap-2 mt-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={!!form.has_rds}
                onChange={(e) => onChange('has_rds', e.target.checked)}
                disabled={disabled}
                className="mt-0.5 w-3.5 h-3.5 rounded"
                style={{ accentColor: 'var(--status-ready)' }}
              />
              <span className="flex-1">
                <span className="block text-[12px] text-neutral-200">
                  Removable deployment system (RDS)
                </span>
                <span className="block text-[10px] text-neutral-500 mt-0.5">
                  Adds +0.15 lb of line wear per jump (D45).
                </span>
              </span>
            </label>
          )}
          {type === 'reserve' && (
            <>
              <FormGrid>
                <Field label="REPACK COUNT (INITIAL)">
                  <input
                    type="number"
                    min="0"
                    value={form.repack_count_initial}
                    onChange={(e) => onChange('repack_count_initial', e.target.value)}
                    disabled={disabled}
                    className={inputCls}
                  />
                </Field>
                <Field label="RIDE COUNT (INITIAL)">
                  <input
                    type="number"
                    min="0"
                    value={form.ride_count_initial}
                    onChange={(e) => onChange('ride_count_initial', e.target.value)}
                    disabled={disabled}
                    className={inputCls}
                  />
                </Field>
              </FormGrid>
              <FormGrid>
                <Field label="REPACK LIMIT">
                  <input
                    type="number"
                    min="0"
                    value={form.repack_limit}
                    onChange={(e) => onChange('repack_limit', e.target.value)}
                    disabled={disabled}
                    className={inputCls}
                  />
                </Field>
                <Field label="RIDE LIMIT">
                  <input
                    type="number"
                    min="0"
                    value={form.ride_limit}
                    onChange={(e) => onChange('ride_limit', e.target.value)}
                    disabled={disabled}
                    className={inputCls}
                  />
                </Field>
              </FormGrid>
            </>
          )}
        </Card>
      )}

      {type === 'main' && (
        <Card>
          <SectionLabel>CURRENT LINESET</SectionLabel>
          <div className="text-[11px] text-neutral-500 mb-2.5 leading-relaxed">
            Clear MATERIAL to record this main as "not yet lined".
            In-place edits preserve the lineset id so historical
            jumps that reference it (D36) keep their wear math
            intact. The reline workflow (R.5) is the right path
            once jumps exist on this lineset.
          </div>
          <FormGrid>
            <Field label="MATERIAL">
              <select
                value={form.lineset_material}
                onChange={(e) => {
                  const m = e.target.value;
                  onChange('lineset_material', m);
                  onChange('lineset_variant', '');
                }}
                disabled={disabled}
                className={inputCls}
              >
                <option value="">— not yet lined —</option>
                {Object.entries(LINE_MATERIALS).map(([k, v]) => (
                  <option key={k} value={k}>{v.label}</option>
                ))}
              </select>
            </Field>
            <Field label={form.lineset_material === 'other' ? 'TYPE (free text)' : 'VARIANT'}>
              {form.lineset_material === 'other' ? (
                <input
                  value={form.lineset_variant}
                  onChange={(e) => onChange('lineset_variant', e.target.value)}
                  disabled={disabled}
                  placeholder="custom line description"
                  className={inputCls}
                />
              ) : (
                <select
                  value={form.lineset_variant}
                  onChange={(e) => {
                    const v = e.target.value;
                    onChange('lineset_variant', v);
                    const variants = LINE_MATERIALS[form.lineset_material]?.variants || [];
                    const found = variants.find((x) => x.value === v);
                    if (found) onChange('lineset_breaking_strength_lb', String(found.strength));
                  }}
                  disabled={disabled || !form.lineset_material}
                  className={inputCls}
                >
                  <option value="">— pick a variant —</option>
                  {(LINE_MATERIALS[form.lineset_material]?.variants || []).map((x) => (
                    <option key={x.value} value={x.value}>
                      {form.lineset_material === 'vectran' ? x.value : `${LINE_MATERIALS[form.lineset_material].label} ${x.value}`}
                      {' '}
                      ({x.strength} lb)
                    </option>
                  ))}
                </select>
              )}
            </Field>
          </FormGrid>
          <FormGrid>
            <Field label="BREAKING STRENGTH (lb)">
              <input
                type="number"
                step="1"
                min="0"
                value={form.lineset_breaking_strength_lb}
                onChange={(e) => onChange('lineset_breaking_strength_lb', e.target.value)}
                disabled={disabled || !form.lineset_material}
                placeholder="auto-fills from variant"
                className={inputCls}
              />
            </Field>
            <Field label="LINE TYPE (composed)">
              <input
                readOnly
                value={composeLineType(form.lineset_material, form.lineset_variant) || '—'}
                className={inputCls}
                style={{ opacity: 0.7, cursor: 'default' }}
              />
            </Field>
          </FormGrid>
          <FormGrid>
            <Field label="INSTALL DATE">
              <input
                type="date"
                value={form.lineset_install_date}
                onChange={(e) => onChange('lineset_install_date', e.target.value)}
                disabled={disabled || !form.lineset_material}
                className={inputCls}
              />
            </Field>
            <Field label="INSTALLED BY (optional)">
              <input
                value={form.lineset_installed_by}
                onChange={(e) => onChange('lineset_installed_by', e.target.value)}
                disabled={disabled || !form.lineset_material}
                placeholder="rigger name"
                className={inputCls}
              />
            </Field>
          </FormGrid>
          <FormGrid>
            <Field label="JUMPS ON LINESET (used gear)">
              <input
                type="number"
                step="1"
                min="0"
                value={form.lineset_jumps_on_lineset_initial}
                onChange={(e) => onChange('lineset_jumps_on_lineset_initial', e.target.value)}
                disabled={disabled || !form.lineset_material}
                placeholder="0 for fresh install"
                className={inputCls}
              />
            </Field>
            <div />
          </FormGrid>
          <div className="text-[11px] text-neutral-500 mt-2 leading-relaxed">
            Exit weight is read live from your jumper profile (D46),
            not snapshotted on the lineset.
          </div>
        </Card>
      )}

      {type === 'aad' && (
        <Card>
          <SectionLabel>AAD MODE & COUNTERS</SectionLabel>
          <FormGrid>
            <Field label="MODE">
              <input
                value={form.mode}
                onChange={(e) => onChange('mode', e.target.value)}
                disabled={disabled}
                className={inputCls}
              />
            </Field>
            <Field label="MODE CHANGEABLE">
              <select
                value={form.is_changeable_mode}
                onChange={(e) => onChange('is_changeable_mode', e.target.value)}
                disabled={disabled}
                className={inputCls}
              >
                <option value="">unknown</option>
                <option value="true">yes</option>
                <option value="false">no</option>
              </select>
            </Field>
          </FormGrid>
          <FormGrid>
            <Field label="JUMPS (INITIAL)">
              <input
                type="number"
                min="0"
                value={form.jump_count_initial}
                onChange={(e) => onChange('jump_count_initial', e.target.value)}
                disabled={disabled}
                className={inputCls}
              />
            </Field>
            <Field label="FIRES (INITIAL)">
              <input
                type="number"
                min="0"
                value={form.fire_count_initial}
                onChange={(e) => onChange('fire_count_initial', e.target.value)}
                disabled={disabled}
                className={inputCls}
              />
            </Field>
          </FormGrid>
        </Card>
      )}

      {type === 'container' && (
        <Card>
          <SectionLabel>GEOMETRY & COUNTERS</SectionLabel>
          <FormGrid>
            <Field label="SIZE (free text)">
              <input
                value={form.size}
                onChange={(e) => onChange('size', e.target.value)}
                disabled={disabled}
                className={inputCls}
              />
            </Field>
            <Field label="JUMPS (INITIAL)">
              <input
                type="number"
                min="0"
                value={form.jump_count_initial}
                onChange={(e) => onChange('jump_count_initial', e.target.value)}
                disabled={disabled}
                className={inputCls}
              />
            </Field>
          </FormGrid>
        </Card>
      )}

      <div className="text-[11px] text-neutral-500 leading-relaxed">
        Edits PUT-replace the record. Fields not exposed here
        (notes_log, lineset_history, current_lineset for mains,
        recert_extensions for reserves) are preserved from the
        on-disk state. Save bumps <span className="font-mono">updated_at</span>.
      </div>
    </>
  );
}


function Footer({
  record, editing, saving, deleting, confirmingDelete,
  onEdit, onCancel, onSave, onDelete,
}) {
  return (
    <div
      className="flex items-center gap-2 px-5 py-3"
      style={{ background: 'var(--surface-1)', borderTop: '0.5px solid var(--border-strong)' }}
    >
      {confirmingDelete ? (
        <span className="text-[11px]" style={{ color: 'var(--status-critical)' }}>
          Move this component to <span className="font-mono">.trash/</span>?
          {record.assigned_rig_id && (
            <> Rig <span className="font-mono">{record.assigned_rig_id}</span> will be left with a dangling reference.</>
          )}
        </span>
      ) : editing ? (
        <span className="text-[11px] text-neutral-500">
          Full-replace PUT. <span className="font-mono">assigned_rig_id</span> stays where it was.
        </span>
      ) : (
        <span className="text-[11px] text-neutral-500">
          Edits XSD-validate and atomically write to the logbook folder.
        </span>
      )}
      <div className="flex-1" />
      <button
        onClick={onCancel}
        disabled={saving || deleting}
        className="px-3 py-1.5 text-[12px] text-neutral-400 transition hover:text-neutral-200 disabled:opacity-40"
      >
        {confirmingDelete ? 'Cancel' : (editing ? 'Cancel' : 'Close')}
      </button>
      {!editing && !confirmingDelete && (
        <button
          onClick={onEdit}
          disabled={saving || deleting}
          className="px-3.5 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition disabled:opacity-50"
          style={{
            background: 'transparent',
            color: 'var(--text)',
            border: '0.5px solid var(--border-strong)',
          }}
        >
          <Pencil className="w-3 h-3" />
          Edit
        </button>
      )}
      {editing && (
        <button
          onClick={onSave}
          disabled={saving}
          className="px-3.5 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition"
          style={{
            background: saving ? 'var(--surface-3)' : 'var(--text)',
            color: saving ? 'var(--text-faint)' : 'var(--bg)',
            cursor: saving ? 'not-allowed' : 'pointer',
          }}
        >
          {saving ? (
            <>
              <Loader2 className="w-3 h-3 animate-spin" />
              Saving…
            </>
          ) : (
            <>
              <Save className="w-3 h-3" />
              Save changes
            </>
          )}
        </button>
      )}
      {!editing && (
        <button
          onClick={onDelete}
          disabled={saving || deleting}
          className="px-3.5 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition disabled:opacity-50"
          style={{
            background: confirmingDelete ? 'var(--status-critical)' : 'transparent',
            color: confirmingDelete ? 'var(--bg)' : 'var(--status-critical)',
            border: confirmingDelete ? '0.5px solid var(--status-critical)' : '0.5px solid rgba(248,113,113,0.4)',
          }}
        >
          {deleting ? (
            <>
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              Deleting…
            </>
          ) : (
            <>
              <Trash2 className="w-3.5 h-3.5" />
              {confirmingDelete ? 'Confirm delete' : 'Delete'}
            </>
          )}
        </button>
      )}
    </div>
  );
}


// --------------------------------------------------------------------- //
// Form ↔ record marshalling
// --------------------------------------------------------------------- //

// Produce the form state from a backend record. Strings stay strings,
// nullable scalars become '' so the inputs don't render "null".
function formFromRecord(type, r) {
  const base = {
    status: r.status || 'active',
    manufacturer: r.manufacturer || '',
    model: r.model || '',
    serial: r.serial || '',
    date_of_manufacture: r.date_of_manufacture || '',
  };
  if (type === 'main') {
    // Seed the lineset fields from the existing current_lineset
    // (if any). lineset_id is preserved through the round-trip
    // so historical jumps that pin to it via D36 keep their
    // pointer valid; clearing the material drops the lineset
    // wholesale on save. R.5 reline gets a separate workflow.
    //
    // install_date defaults to today when the main has no prior
    // lineset, mirroring AddComponentModal's emptyForm. Without
    // this default, picking a material and hitting Save fires a
    // PUT with install_date=null, which Pydantic's required
    // _date field rejects with 422.
    const ls = r.current_lineset || null;
    const { material, variant } = ls
      ? decomposeLineType(ls.line_type || '')
      : { material: '', variant: '' };
    return {
      ...base,
      size_sqft: r.size_sqft != null ? String(r.size_sqft) : '',
      // D45 RDS flag round-tripped as a real boolean. Defaults to
      // false when the record predates the field on disk.
      has_rds: !!r.has_rds,
      jump_count_initial: String(r.jump_count_initial ?? 0),
      lineset_id: ls?.id || '',
      lineset_material: material,
      lineset_variant: variant,
      lineset_breaking_strength_lb: ls?.breaking_strength_lb != null
        ? String(ls.breaking_strength_lb) : '',
      lineset_install_date: ls?.install_date || new Date().toISOString().slice(0, 10),
      lineset_installed_by: ls?.installed_by || '',
      lineset_jumps_on_lineset_initial:
        String(ls?.jumps_on_lineset_initial ?? 0),
    };
  }
  if (type === 'reserve') {
    return {
      ...base,
      size_sqft: r.size_sqft != null ? String(r.size_sqft) : '',
      repack_count_initial: String(r.repack_count_initial ?? 0),
      ride_count_initial: String(r.ride_count_initial ?? 0),
      repack_limit: r.repack_limit != null ? String(r.repack_limit) : '',
      ride_limit: r.ride_limit != null ? String(r.ride_limit) : '',
    };
  }
  if (type === 'aad') {
    return {
      ...base,
      mode: r.mode || '',
      is_changeable_mode:
        r.is_changeable_mode === true ? 'true'
        : r.is_changeable_mode === false ? 'false'
        : '',
      jump_count_initial: String(r.jump_count_initial ?? 0),
      fire_count_initial: String(r.fire_count_initial ?? 0),
    };
  }
  if (type === 'container') {
    return {
      ...base,
      size: r.size || '',
      jump_count_initial: String(r.jump_count_initial ?? 0),
    };
  }
  return base;
}


// Compose the PUT payload. We start from the original record so that
// fields the user can't see (notes_log, lineset_history, current_lineset
// for mains, recert_extensions for reserves, default_environment) round-
// trip without loss. Then we overlay the edited form. assigned_rig_id,
// id, created_at, updated_at are stripped — *Update models reject them
// (extra="forbid").
function buildUpdatePayload(type, record, form) {
  const numOrZero = (v) => {
    const n = parseInt(v, 10);
    return Number.isFinite(n) ? n : 0;
  };
  const numOrNull = (v) => {
    if (v === '' || v == null) return null;
    const n = parseInt(v, 10);
    return Number.isFinite(n) ? n : null;
  };
  const floatOrNull = (v) => {
    if (v === '' || v == null) return null;
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : null;
  };

  const stripped = { ...record };
  delete stripped.id;
  delete stripped.assigned_rig_id;
  delete stripped.created_at;
  delete stripped.updated_at;

  const overlay = {
    status: form.status,
    manufacturer: form.manufacturer.trim() || null,
    model: form.model.trim() || null,
    serial: form.serial.trim() || null,
    date_of_manufacture: form.date_of_manufacture || null,
  };

  if (type === 'main') {
    // Compose current_lineset from the form. Cleared MATERIAL →
    // current_lineset becomes null (the main is "not yet lined").
    // Otherwise we build a Lineset block; if there was a prior
    // lineset on disk we preserve its id so historical jumps that
    // pin to it via D36 don't dangle. Pydantic auto-generates a
    // fresh id when the field is omitted (default_factory=uuid4),
    // which is what we want when the user is recording a lineset
    // for the first time.
    const composed = composeLineType(form.lineset_material, form.lineset_variant);
    let current_lineset = null;
    if (composed) {
      current_lineset = {
        line_type: composed,
        breaking_strength_lb: floatOrNull(form.lineset_breaking_strength_lb),
        install_date: form.lineset_install_date || null,
        installed_by: form.lineset_installed_by.trim() || null,
        jumps_on_lineset_initial: numOrZero(form.lineset_jumps_on_lineset_initial),
      };
      if (form.lineset_id) current_lineset.id = form.lineset_id;
    }
    return {
      ...stripped,
      ...overlay,
      size_sqft: floatOrNull(form.size_sqft),
      // D45: round-trip the RDS flag through the PUT. Coerced to a
      // real boolean so a stray string from the form state can't
      // slip through as Truthy-but-not-True.
      has_rds: !!form.has_rds,
      jump_count_initial: numOrZero(form.jump_count_initial),
      current_lineset,
    };
  }
  if (type === 'reserve') {
    return {
      ...stripped,
      ...overlay,
      size_sqft: floatOrNull(form.size_sqft),
      repack_limit: numOrNull(form.repack_limit),
      ride_limit: numOrNull(form.ride_limit),
      repack_count_initial: numOrZero(form.repack_count_initial),
      ride_count_initial: numOrZero(form.ride_count_initial),
    };
  }
  if (type === 'aad') {
    return {
      ...stripped,
      ...overlay,
      mode: form.mode.trim() || null,
      is_changeable_mode:
        form.is_changeable_mode === 'true' ? true
        : form.is_changeable_mode === 'false' ? false
        : null,
      jump_count_initial: numOrZero(form.jump_count_initial),
      fire_count_initial: numOrZero(form.fire_count_initial),
    };
  }
  if (type === 'container') {
    return {
      ...stripped,
      ...overlay,
      size: form.size.trim() || null,
      jump_count_initial: numOrZero(form.jump_count_initial),
    };
  }
  return { ...stripped, ...overlay };
}


// Swap picker (S.4). Renders a card with the list of swap-eligible
// mains. Selection POSTs swap_main and the parent reloads. The
// filter (active + unassigned-or-on-this-rig) lives in the parent's
// handleOpenSwap; this component just renders the list it's given.
function SwapPicker({ options, loading, swapping, onCancel, onPick }) {
  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{ background: 'var(--bg)', border: '0.5px solid var(--border-strong)' }}
    >
      <div className="flex items-center justify-between px-4 py-2.5"
           style={{ borderBottom: '0.5px solid var(--border-strong)' }}>
        <span className="text-[10px] tracking-[0.25em] text-neutral-500 font-medium">
          PICK A MAIN FROM INVENTORY
        </span>
        <button
          onClick={onCancel}
          disabled={swapping}
          className="text-[11px] text-neutral-400 transition hover:text-neutral-200 disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
      {loading && (
        <div className="px-4 py-6 text-center text-[12px] text-neutral-500 flex items-center justify-center gap-2">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          Loading inventory…
        </div>
      )}
      {!loading && options && options.length === 0 && (
        <div className="px-4 py-6 text-center text-[12px] text-neutral-500">
          No active, unassigned mains available. Add one in Inventory first.
        </div>
      )}
      {!loading && options && options.length > 0 && options.map((m, i) => (
        <button
          key={m.id}
          onClick={() => onPick(m.id)}
          disabled={swapping}
          className="w-full text-left px-4 py-3 flex items-center gap-3 transition hover:bg-neutral-800/50 disabled:opacity-50"
          style={{ borderTop: i > 0 ? '0.5px solid var(--surface-2)' : 'none' }}
        >
          <Check className="w-3.5 h-3.5 text-neutral-500 flex-shrink-0" />
          <div className="flex-1 min-w-0">
            <div className="text-[13px] text-neutral-100 truncate">
              {m.manufacturer || 'unknown'} {m.model || ''}
              {m.size_sqft != null && (
                <span className="text-neutral-500"> · {Number(m.size_sqft)} sqft</span>
              )}
            </div>
            <div className="text-[11px] text-neutral-500 font-mono mt-0.5">
              {m.serial ? `SN ${m.serial}` : 'no serial'}
              {' · '}
              {m.jump_count_initial ?? 0} initial jumps
            </div>
          </div>
          {swapping && <Loader2 className="w-3 h-3 animate-spin text-neutral-500" />}
        </button>
      ))}
    </div>
  );
}


// --------------------------------------------------------------------- //
// Presentational primitives (mirror JumpDetailModal / AddComponentModal)
// --------------------------------------------------------------------- //

function Card({ children }) {
  return (
    <div className="rounded-xl p-4" style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)' }}>
      {children}
    </div>
  );
}

function SectionLabel({ children }) {
  return (
    <div className="text-[10px] tracking-[0.25em] text-neutral-500 font-medium mb-2.5">
      {children}
    </div>
  );
}

function KVGrid({ children }) {
  return <div className="grid grid-cols-2 gap-x-5 gap-y-2.5">{children}</div>;
}

function KV({ label, children }) {
  return (
    <div>
      <div className="text-[10px] tracking-[0.15em] text-neutral-500 uppercase mb-1">
        {label}
      </div>
      <div className="text-[13px] text-neutral-200">{children}</div>
    </div>
  );
}

function Empty() {
  return <span className="text-neutral-600 italic text-[12px]">—</span>;
}

function StatusBadge({ status }) {
  const palette = {
    active:  { fg: 'var(--status-ready)', bg: 'rgba(52,211,153,0.08)', bd: 'rgba(52,211,153,0.25)' },
    retired: { fg: 'var(--status-critical)', bg: 'rgba(248,113,113,0.08)', bd: 'rgba(248,113,113,0.25)' },
    lost:    { fg: 'var(--status-watch)', bg: 'rgba(251,191,36,0.08)', bd: 'rgba(251,191,36,0.25)' },
  };
  const p = palette[status] || palette.active;
  return (
    <span
      className="inline-block text-[10px] tracking-[0.15em] px-2 py-0.5 rounded-full uppercase"
      style={{ color: p.fg, background: p.bg, border: `0.5px solid ${p.bd}` }}
    >
      {status}
    </span>
  );
}

function FormGrid({ children }) {
  return <div className="grid grid-cols-2 gap-3">{children}</div>;
}

function Field({ label, children }) {
  return (
    <div>
      <div className="text-[9px] tracking-[0.2em] text-neutral-500 font-medium mb-1">
        {label}
      </div>
      {children}
    </div>
  );
}

const inputCls =
  'w-full rounded-md px-3 py-1.5 text-[13px] text-neutral-100 bg-[var(--bg)] border border-neutral-800 focus:border-neutral-600 focus:outline-none disabled:opacity-50';


function ErrorBanner({ error }) {
  const isApi = error instanceof ApiError;
  const problem = isApi ? error.problem : null;
  return (
    <div
      className="p-4 rounded-xl flex items-start gap-3"
      style={{ background: 'rgba(248,113,113,0.05)', border: '0.5px solid rgba(248,113,113,0.25)' }}
    >
      <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: 'var(--status-critical)' }} />
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-medium text-neutral-100">
          {isApi ? (problem?.title || 'Request failed') : "Couldn't load"}
        </div>
        <div className="text-[12px] text-neutral-400 mt-1">
          {problem?.detail || error.message}
        </div>
        {isApi && Array.isArray(problem?.errors) && problem.errors.length > 0 && (
          <ul className="mt-2 ml-3 list-disc space-y-0.5">
            {problem.errors.map((e, i) => (
              <li key={i} className="text-[11px] font-mono text-neutral-400">
                <span className="text-neutral-300">{e.pointer}</span>: {e.detail}
              </li>
            ))}
          </ul>
        )}
        {isApi && problem?.type && (
          <div className="text-[11px] text-neutral-500 mt-2 font-mono">
            type: {problem.type} · status: {problem.status}
            {error.requestId && <> · request: {error.requestId}</>}
          </div>
        )}
      </div>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="rounded-xl p-4"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)' }}
        >
          <div className="h-3 rounded mb-3" style={{ background: 'var(--surface-2)', width: 80 }} />
          <div className="grid grid-cols-2 gap-3">
            <div className="h-4 rounded" style={{ background: 'var(--surface-2)' }} />
            <div className="h-4 rounded" style={{ background: 'var(--surface-2)' }} />
            <div className="h-4 rounded" style={{ background: 'var(--surface-2)' }} />
            <div className="h-4 rounded" style={{ background: 'var(--surface-2)' }} />
          </div>
        </div>
      ))}
    </div>
  );
}
