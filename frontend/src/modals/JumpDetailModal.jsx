import React, { useState, useEffect, useRef } from 'react';
import {
  X, Trash2, AlertTriangle, Loader2, Paperclip, MapPin, Calendar,
  Plane, ArrowDownToLine, ArrowUpFromLine, Pencil, Plus, ExternalLink, FolderOpen,
  Package,
} from 'lucide-react';
import {
  getJump, listJumpFiles, trackJumpFiles, addJumpAttachments,
  deleteJumpAttachment, deleteJump, getRig, getMain, ApiError,
} from '../api';
import { useAltitudeUnit, metersToDisplay, altitudeSuffix } from '../units';

// Bridge to the pywebview JS API for OS-level actions (open, reveal).
// Returns null in browser dev mode where pywebview is absent.
function pywebviewApi() {
  if (typeof window !== 'undefined' && window.pywebview && window.pywebview.api) {
    return window.pywebview.api;
  }
  return null;
}

export default function JumpDetailModal({ jumpId, onClose, onDeleted, onEdit }) {
  const [jump, setJump] = useState(null); // null = not loaded
  // folderFiles is the merged list (tracked + untracked) from the
  // /files endpoint. Loaded in parallel with the Jump so the modal
  // shows folder contents that may go beyond what's in jump.xml — a
  // file added via the OS file manager appears here without a manifest edit.
  const [folderFiles, setFolderFiles] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Fetch the full Jump and the folder file listing every time the
  // modal opens for a new id. Both go in parallel — the modal can
  // render the Jump's metadata without waiting for the file scan.
  useEffect(() => {
    if (!jumpId) {
      setJump(null);
      setFolderFiles(null);
      setError(null);
      setConfirming(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setFolderFiles(null);
    getJump(jumpId)
      .then((data) => { if (!cancelled) setJump(data); })
      .catch((err) => { if (!cancelled) setError(err); })
      .finally(() => { if (!cancelled) setLoading(false); });
    listJumpFiles(jumpId)
      .then((data) => { if (!cancelled) setFolderFiles(data || []); })
      .catch(() => { /* tolerate — keep the rest of the modal usable */ });
    return () => { cancelled = true; };
  }, [jumpId]);

  // Lock body scroll while modal is open.
  useEffect(() => {
    if (jumpId) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => { document.body.style.overflow = ''; };
  }, [jumpId]);

  if (!jumpId) return null;

  async function handleDelete() {
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      await deleteJump(jumpId);
      onDeleted(jumpId);
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <div
        onClick={deleting ? undefined : onClose}
        className="fixed inset-0 z-40"
        style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)' }}
      />
      <div className="fixed inset-0 z-50 flex items-start justify-center p-6 pointer-events-none overflow-y-auto">
        <div
          onClick={(e) => e.stopPropagation()}
          className="rounded-2xl w-full max-w-2xl pointer-events-auto mt-10 mb-10 flex flex-col"
          style={{ background: 'var(--surface-1)', border: '0.5px solid var(--border-strong)', maxHeight: 'calc(100vh - 80px)' }}
        >
          <Header jump={jump} loading={loading} onClose={onClose} />
          <div className="overflow-y-auto flex-1 p-5 space-y-3">
            {error && <ErrorBanner error={error} />}
            {loading && !jump && <LoadingSkeleton />}
            {jump && (
              <Body
                jump={jump}
                folderFiles={folderFiles}
                onTracked={(updated, fresh) => {
                  setJump(updated);
                  setFolderFiles(fresh);
                }}
              />
            )}
          </div>
          {jump && (
            <Footer
              jump={jump}
              confirming={confirming}
              deleting={deleting}
              onCancel={confirming ? () => setConfirming(false) : onClose}
              onDelete={handleDelete}
              onEdit={() => onEdit && onEdit(jump)}
            />
          )}
        </div>
      </div>
    </>
  );
}

function Header({ jump, loading, onClose }) {
  return (
    <div
      className="flex items-start justify-between px-6 pt-5 pb-4"
      style={{ borderBottom: '0.5px solid var(--border-strong)' }}
    >
      <div className="min-w-0 flex-1 pr-4">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-[9px] tracking-[0.25em] text-neutral-500 font-medium font-mono">
            {jump ? `#${jump.jump_number}` : '#…'}
          </span>
          {jump && (
            <span
              className="inline-block text-[9px] tracking-[0.15em] px-2 py-0.5 rounded-full"
              style={{
                color: 'var(--status-ready)',
                background: 'rgba(52,211,153,0.08)',
                border: '0.5px solid rgba(52,211,153,0.25)',
              }}
            >
              XSD VALID
            </span>
          )}
        </div>
        <div className="text-[20px] font-medium tracking-tight truncate">
          {loading
            ? <span className="text-neutral-500">Loading…</span>
            : (jump?.title || <span className="text-neutral-500 italic">Untitled jump</span>)}
        </div>
        {jump && (
          <div className="text-[12px] text-neutral-500 mt-0.5">
            {formatDate(jump.date)} · {jump.dropzone}
          </div>
        )}
      </div>
      <button
        onClick={onClose}
        className="w-8 h-8 rounded-lg flex items-center justify-center transition hover:bg-neutral-800 flex-shrink-0"
        style={{ background: 'var(--surface-2)' }}
      >
        <X className="w-3.5 h-3.5 text-neutral-400" />
      </button>
    </div>
  );
}

function Body({ jump, folderFiles, onTracked }) {
  const [altitudeUnit] = useAltitudeUnit();
  const altitudeSuffixUpper = altitudeSuffix(altitudeUnit).toLowerCase();
  return (
    <>
      {/* Match the Log-a-jump modal: flat sections separated by
          hairlines, first section sits flush with the body top. */}
      <style>{`
        .jump-detail-section:first-of-type { border-top: none !important; padding-top: 0 !important; }
      `}</style>
      <Card>
        <SectionLabel>BASICS</SectionLabel>
        <KVGrid>
          <KV label="Date" icon={<Calendar />}>{formatDate(jump.date)}</KV>
          <KV label="Dropzone" icon={<MapPin />}>{jump.dropzone}</KV>
          <KV label="Aircraft" icon={<Plane />}>
            {jump.aircraft || <Empty />}
          </KV>
          <KV label="Discipline">
            {jump.discipline || <Empty />}
          </KV>
        </KVGrid>
      </Card>

      {/* R.2.2-light.c: GEAR card surfaces the rig + main canopy
          when jump.rig_id is set. Only renders if the link exists,
          so legacy jumps and quick-log jumps without a rig don't
          get an empty card. */}
      {jump.rig_id && <GearCard rigId={jump.rig_id} />}

      <Card>
        <SectionLabel>ALTITUDES &amp; TIME</SectionLabel>
        <KVGrid>
          <KV label="Exit altitude" icon={<ArrowUpFromLine />}>
            <span className="font-mono">
              {metersToDisplay(jump.exit_altitude_m, altitudeUnit).toLocaleString()}
            </span>
            <span className="text-neutral-500"> {altitudeSuffixUpper}</span>
          </KV>
          <KV label="Deployment altitude" icon={<ArrowDownToLine />}>
            <span className="font-mono">
              {metersToDisplay(jump.deployment_altitude_m, altitudeUnit).toLocaleString()}
            </span>
            <span className="text-neutral-500"> {altitudeSuffixUpper}</span>
          </KV>
          <KV label="Freefall time">
            {jump.freefall_time_s != null
              ? <span className="font-mono">{Math.floor(jump.freefall_time_s / 60)}:{String(jump.freefall_time_s % 60).padStart(2, '0')}</span>
              : <Empty />}
          </KV>
          <KV label="Drop">
            <span className="font-mono">
              {metersToDisplay(
                jump.exit_altitude_m - jump.deployment_altitude_m,
                altitudeUnit,
              ).toLocaleString()}
            </span>
            <span className="text-neutral-500"> {altitudeSuffixUpper}</span>
          </KV>
        </KVGrid>
      </Card>

      {jump.notes && (
        <Card>
          <SectionLabel>NOTES</SectionLabel>
          <div className="text-[13px] text-neutral-200 leading-relaxed whitespace-pre-wrap">
            {jump.notes}
          </div>
        </Card>
      )}

      <AttachmentsCard
        jumpId={jump.id}
        folderFiles={folderFiles}
        fallback={jump.attachments}
        onTracked={onTracked}
      />

      <Card>
        <SectionLabel>FORENSICS</SectionLabel>
        <div className="grid grid-cols-[110px_1fr] gap-y-1.5 text-[12px]">
          <span className="text-neutral-500">ID</span>
          <span className="font-mono text-neutral-300 truncate" title={jump.id}>{jump.id}</span>
          {jump.created_at && (
            <>
              <span className="text-neutral-500">Created at</span>
              <span className="font-mono text-neutral-400">{jump.created_at}</span>
            </>
          )}
          <span className="text-neutral-500">Edited at</span>
          {jump.updated_at && jump.updated_at !== jump.created_at ? (
            <span className="font-mono text-neutral-400">{jump.updated_at}</span>
          ) : (
            <span className="text-neutral-600 italic">never edited</span>
          )}
        </div>
      </Card>
    </>
  );
}

function AttachmentsCard({ jumpId, folderFiles, fallback, onTracked }) {
  const fileInputRef = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);

  // Tracks which filename has its trash button armed. Only one row
  // can be in the "confirm" state at a time — clicking another row's
  // trash automatically cancels the previous arming.
  const [confirmingDelete, setConfirmingDelete] = useState(null);
  const [deletingFilename, setDeletingFilename] = useState(null);

  function handleOpen(filename) {
    const api = pywebviewApi();
    if (!api) return;
    api.open_jump_attachment(jumpId, filename);
  }

  async function handleDelete(filename) {
    if (confirmingDelete !== filename) {
      setConfirmingDelete(filename);
      return;
    }
    setDeletingFilename(filename);
    setUploadError(null);
    try {
      const updatedJump = await deleteJumpAttachment(jumpId, filename);
      const fresh = await listJumpFiles(jumpId);
      onTracked(updatedJump, fresh);
      setConfirmingDelete(null);
    } catch (err) {
      setUploadError(err);
    } finally {
      setDeletingFilename(null);
    }
  }

  async function handleFilesSelected(e) {
    const picked = Array.from(e.target.files || []);
    e.target.value = '';
    if (picked.length === 0) return;
    setUploading(true);
    setUploadError(null);
    try {
      const updatedJump = await addJumpAttachments(jumpId, picked);
      const fresh = await listJumpFiles(jumpId);
      onTracked(updatedJump, fresh);
    } catch (err) {
      setUploadError(err);
    } finally {
      setUploading(false);
    }
  }
  // While the /files request is in flight, fall back to the
  // jump.xml-recorded attachments so the section isn't empty during
  // the brief load. Once folderFiles arrives, it replaces fallback —
  // it's a strict superset (tracked + untracked).
  const sourceList = folderFiles ?? (fallback || []).map((a) => ({
    filename: a.filename,
    size: a.size,
    tracked: true,
    sha256: a.sha256,
    content_type: a.content_type,
  }));

  const tracked = sourceList.filter((f) => f.tracked);
  const untracked = sourceList.filter((f) => !f.tracked);

  // Per-file tracking is in flight when the user clicks "Track".
  // Stored as a Set of filenames so two clicks on different files
  // can run in parallel without state thrashing.
  const [tracking, setTracking] = useState(() => new Set());
  const [trackError, setTrackError] = useState(null);

  async function handleTrack(filenames) {
    setTracking((prev) => {
      const next = new Set(prev);
      filenames.forEach((f) => next.add(f));
      return next;
    });
    setTrackError(null);
    try {
      const updatedJump = await trackJumpFiles(jumpId, filenames);
      // Re-fetch /files for the authoritative tracked/untracked split.
      const fresh = await listJumpFiles(jumpId);
      onTracked(updatedJump, fresh);
    } catch (err) {
      setTrackError(err);
    } finally {
      setTracking((prev) => {
        const next = new Set(prev);
        filenames.forEach((f) => next.delete(f));
        return next;
      });
    }
  }

  return (
    <Card>
      <div className="flex items-center justify-between mb-2.5 gap-2">
        <SectionLabel>
          ATTACHMENTS
          {sourceList.length > 0 && (
            <span className="font-mono text-neutral-500 ml-2 normal-case tracking-normal">
              ({tracked.length} tracked{untracked.length > 0 ? `, ${untracked.length} untracked` : ''})
            </span>
          )}
        </SectionLabel>
        <div className="flex items-center gap-2">
          {untracked.length > 1 && (
            <button
              onClick={() => handleTrack(untracked.map((f) => f.filename))}
              disabled={tracking.size > 0 || uploading}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-medium transition disabled:opacity-50"
              style={{
                background: 'transparent',
                color: 'var(--status-watch)',
                border: '0.5px solid rgba(251,191,36,0.4)',
              }}
            >
              <Plus className="w-3 h-3" strokeWidth={2.2} />
              Track all
            </button>
          )}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            onChange={handleFilesSelected}
            style={{ display: 'none' }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading || tracking.size > 0}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-medium transition disabled:opacity-50"
            style={{
              background: 'transparent',
              color: 'var(--text)',
              border: '0.5px solid var(--border-strong)',
            }}
            title="Pick files to upload into this jump's folder"
          >
            {uploading ? (
              <>
                <Loader2 className="w-3 h-3 animate-spin" />
                Uploading…
              </>
            ) : (
              <>
                <Plus className="w-3 h-3" strokeWidth={2.2} />
                Add files…
              </>
            )}
          </button>
        </div>
      </div>
      {uploadError && (
        <div
          className="mb-2 p-2 rounded-md text-[11px]"
          style={{ background: 'rgba(248,113,113,0.05)', border: '0.5px solid rgba(248,113,113,0.25)', color: 'var(--status-critical)' }}
        >
          {uploadError instanceof ApiError
            ? (uploadError.problem?.detail || uploadError.message)
            : uploadError.message}
          {uploadError instanceof ApiError && uploadError.problem?.errors?.length > 0 && (
            <ul className="mt-1 ml-3 list-disc">
              {uploadError.problem.errors.map((e, i) => (
                <li key={i} className="font-mono text-neutral-400">
                  <span className="text-neutral-300">{e.pointer}</span>: {e.detail}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {sourceList.length === 0 ? (
        <div className="text-[12px] text-neutral-500 italic">No attachments on this jump.</div>
      ) : (
        <div className="space-y-1.5">
          {tracked.map((f) => (
            <FileRow
              key={`t-${f.filename}`}
              file={f}
              onOpen={() => handleOpen(f.filename)}
              onDelete={() => handleDelete(f.filename)}
              confirmingDelete={confirmingDelete === f.filename}
              deleting={deletingFilename === f.filename}
            />
          ))}
          {untracked.length > 0 && (
            <div className="text-[10px] tracking-[0.15em] text-neutral-500 font-medium pt-2 mt-1.5"
                 style={{ borderTop: '0.5px solid var(--border-strong)' }}>
              UNTRACKED — ADDED VIA FILE MANAGER
            </div>
          )}
          {untracked.map((f) => (
            <FileRow
              key={`u-${f.filename}`}
              file={f}
              busy={tracking.has(f.filename)}
              onTrack={() => handleTrack([f.filename])}
              onOpen={() => handleOpen(f.filename)}
            />
          ))}
          {untracked.length > 0 && (
            <div className="text-[11px] text-neutral-500 mt-2 leading-relaxed">
              Click <span className="text-neutral-400">Track</span> to ingest a file into{' '}
              <span className="font-mono text-neutral-400">jump.xml</span> +{' '}
              <span className="font-mono text-neutral-400">SHA256SUMS</span>. The file's bytes don't move —
              the manifest is updated in place.
            </div>
          )}
          {trackError && (
            <div className="text-[11px] mt-2" style={{ color: 'var(--status-critical)' }}>
              {trackError instanceof ApiError
                ? (trackError.problem?.detail || trackError.message)
                : trackError.message}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function FileRow({
  file, busy, onTrack, onOpen, onDelete, confirmingDelete, deleting,
}) {
  const tracked = file.tracked;
  return (
    <div
      className="flex items-center gap-2 px-3 py-2 rounded-md"
      style={{
        background: 'var(--bg)',
        border: tracked
          ? '0.5px solid var(--border-strong)'
          : '0.5px solid rgba(251,191,36,0.25)',
      }}
    >
      <Paperclip
        className="w-3 h-3 flex-shrink-0"
        style={{ color: tracked ? 'var(--text-faint)' : 'var(--status-watch)' }}
      />
      <div className="flex-1 min-w-0">
        <div className="text-[13px] text-neutral-200 truncate font-mono">{file.filename}</div>
        <div className="text-[10px] text-neutral-500 font-mono mt-0.5">
          {file.content_type || 'unknown type'} · {formatBytes(file.size)}
          {file.sha256 && <> · sha256 {file.sha256.slice(0, 8)}…</>}
        </div>
      </div>
      {onOpen && !confirmingDelete && (
        <button
          onClick={onOpen}
          disabled={deleting}
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-medium transition flex-shrink-0 hover:bg-neutral-800/50 disabled:opacity-40"
          style={{
            background: 'transparent',
            color: 'var(--text-muted)',
            border: '0.5px solid var(--border-strong)',
          }}
          title="Open in default app"
        >
          <ExternalLink className="w-2.5 h-2.5" />
          Open
        </button>
      )}
      {!tracked && onTrack && (
        <button
          onClick={onTrack}
          disabled={busy}
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-medium transition disabled:opacity-50 flex-shrink-0"
          style={{
            background: 'rgba(251,191,36,0.08)',
            color: 'var(--status-watch)',
            border: '0.5px solid rgba(251,191,36,0.4)',
          }}
        >
          {busy ? (
            <>
              <Loader2 className="w-2.5 h-2.5 animate-spin" />
              Tracking…
            </>
          ) : (
            <>
              <Plus className="w-2.5 h-2.5" strokeWidth={2.4} />
              Track
            </>
          )}
        </button>
      )}
      {tracked && onDelete && (
        <button
          onClick={onDelete}
          disabled={deleting}
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-medium transition flex-shrink-0 disabled:opacity-50"
          style={{
            background: confirmingDelete ? 'var(--status-critical)' : 'transparent',
            color: confirmingDelete ? 'var(--bg)' : 'var(--status-critical)',
            border: confirmingDelete ? '0.5px solid var(--status-critical)' : '0.5px solid rgba(248,113,113,0.4)',
          }}
          title={confirmingDelete ? 'Click again to confirm' : 'Delete attachment'}
        >
          {deleting ? (
            <>
              <Loader2 className="w-2.5 h-2.5 animate-spin" />
              Deleting…
            </>
          ) : confirmingDelete ? (
            <>
              <Trash2 className="w-2.5 h-2.5" />
              Confirm
            </>
          ) : (
            <Trash2 className="w-2.5 h-2.5" />
          )}
        </button>
      )}
    </div>
  );
}

function Footer({ jump, confirming, deleting, onCancel, onDelete, onEdit }) {
  function handleRevealFolder() {
    const api = pywebviewApi();
    if (!api || !jump) return;
    api.reveal_jump_folder(jump.id);
  }

  return (
    <div
      className="flex items-center gap-2 px-5 py-3"
      style={{ background: 'var(--surface-1)', borderTop: '0.5px solid var(--border-strong)' }}
    >
      {confirming ? (
        <span className="text-[11px]" style={{ color: 'var(--status-critical)' }}>
          Move this jump to <span className="font-mono">.trash/</span>?
        </span>
      ) : (
        <span className="text-[11px] text-neutral-500">
          Edits are metadata-only. Deletes soft-delete to <span className="font-mono text-neutral-400">.trash/</span>.
        </span>
      )}
      <div className="flex-1" />
      <button
        onClick={onCancel}
        disabled={deleting}
        className="px-3 py-1.5 text-[12px] text-neutral-400 transition hover:text-neutral-200 disabled:opacity-40"
      >
        {confirming ? 'Cancel' : 'Close'}
      </button>
      {!confirming && (
        <button
          onClick={handleRevealFolder}
          disabled={deleting}
          className="px-3.5 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition disabled:opacity-50"
          style={{
            background: 'transparent',
            color: 'var(--text-muted)',
            border: '0.5px solid var(--border-strong)',
          }}
          title="Open this jump's folder in the OS file manager"
        >
          <FolderOpen className="w-3 h-3" />
          Reveal folder
        </button>
      )}
      {!confirming && onEdit && (
        <button
          onClick={onEdit}
          disabled={deleting}
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
      <button
        onClick={onDelete}
        disabled={deleting}
        className="px-3.5 py-1.5 rounded-md text-[12px] font-medium flex items-center gap-1.5 transition disabled:opacity-50"
        style={{
          background: confirming ? 'var(--status-critical)' : 'transparent',
          color: confirming ? 'var(--bg)' : 'var(--status-critical)',
          border: confirming ? '0.5px solid var(--status-critical)' : '0.5px solid rgba(248,113,113,0.4)',
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
            {confirming ? 'Confirm delete' : 'Delete'}
          </>
        )}
      </button>
    </div>
  );
}

// R.2.2-light.c: GEAR card for the rig + main canopy at jump time.
//
// Resolves the jump's rig_id → rig record → main canopy record
// via two cascading API calls. Shown as a dedicated card so the
// link between rig and main is visually obvious.
//
// Caveat (read me before R.2.3): the main shown here is the rig's
// CURRENT main, NOT the main that was on the rig at jump time.
// For now we accept this — the user typically doesn't swap mains
// often and a stale display is acceptable. R.2.3 will replace
// this with reads from rig-snapshot.xml so the display shows the
// frozen-at-log-time main; the GearCard component shape stays the
// same, only the data source changes.
function GearCard({ rigId }) {
  const [rig, setRig] = useState(null);
  const [main, setMain] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!rigId) return;
    let cancelled = false;
    setFailed(false);
    setRig(null);
    setMain(null);
    getRig(rigId)
      .then((r) => {
        if (cancelled) return;
        setRig(r);
        if (!r.current_main_id) return;
        return getMain(r.current_main_id).then((m) => {
          if (!cancelled) setMain(m);
        });
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [rigId]);

  // Format the main as a single-line label, mirroring the
  // LogJumpModal RigPicker chip ("manufacturer model — N sqft").
  // Manufacturer / model are optional on the Main model; fall
  // back gracefully.
  const mainLabel = (() => {
    if (!main) return null;
    const parts = [];
    if (main.manufacturer) parts.push(main.manufacturer);
    if (main.model) parts.push(main.model);
    let label = parts.join(' ');
    if (main.size_sqft != null) {
      const sz = String(Number(main.size_sqft));
      label = label ? `${label} — ${sz} sqft` : `${sz} sqft`;
    }
    return label || 'main canopy on this rig';
  })();

  return (
    <Card>
      <SectionLabel>GEAR</SectionLabel>
      <KVGrid>
        <KV label="Rig" icon={<Package />}>
          {rig ? rig.nickname : failed ? <FailedSentinel /> : <LoadingDots />}
        </KV>
        <KV label="Main canopy">
          {mainLabel ? (
            mainLabel
          ) : failed ? (
            <FailedSentinel />
          ) : rig && !rig.current_main_id ? (
            <Empty />
          ) : (
            <LoadingDots />
          )}
        </KV>
      </KVGrid>
    </Card>
  );
}

function FailedSentinel() {
  return (
    <span className="text-amber-400/80 text-[12px] italic">
      info unavailable
    </span>
  );
}

function LoadingDots() {
  return <span className="text-neutral-600 text-[12px]">…</span>;
}

// Matches the Log-a-jump modal: sections are flat (no card chrome),
// separated by a hairline rule above. The first section in a body
// skips the rule via the `:first-of-type` selector — keep sections
// as direct children of a single wrapper so it applies.
function Card({ children }) {
  return (
    <section
      style={{
        padding: '20px 0',
        borderTop: '1px solid var(--border)',
      }}
      className="jump-detail-section"
    >
      {children}
    </section>
  );
}

function SectionLabel({ children }) {
  return (
    <div
      className="font-medium"
      style={{
        fontSize: 11, color: 'var(--text-muted)',
        letterSpacing: '0.08em', textTransform: 'uppercase',
        marginBottom: 14,
      }}
    >
      {children}
    </div>
  );
}

function KVGrid({ children }) {
  return <div className="grid grid-cols-2" style={{ gap: '16px 24px' }}>{children}</div>;
}

function KV({ label, icon, children }) {
  return (
    <div>
      <div
        className="flex items-center gap-1.5"
        style={{
          fontSize: 11, color: 'var(--text-muted)',
          letterSpacing: '0.08em', textTransform: 'uppercase',
          marginBottom: 4,
        }}
      >
        {icon && React.cloneElement(icon, { className: 'w-2.5 h-2.5', strokeWidth: 1.8 })}
        <span>{label}</span>
      </div>
      <div style={{ fontSize: 14, color: 'var(--text)' }}>{children}</div>
    </div>
  );
}

function Empty() {
  return <span className="text-neutral-600 italic text-[12px]">—</span>;
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

function ErrorBanner({ error }) {
  const isApi = error instanceof ApiError;
  const problem = isApi ? error.problem : null;
  return (
    <div
      className="p-4 rounded-xl flex items-start gap-3"
      style={{
        background: 'rgba(248,113,113,0.05)',
        border: '0.5px solid rgba(248,113,113,0.25)',
      }}
    >
      <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: 'var(--status-critical)' }} />
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-medium text-neutral-100">
          {isApi ? (problem?.title || 'Request failed') : "Couldn't load"}
        </div>
        <div className="text-[12px] text-neutral-400 mt-1">
          {problem?.detail || error.message}
        </div>
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

function formatDate(iso) {
  if (!iso) return '';
  // YYYY-MM-DD → "Apr 20, 2026"
  const [y, m, d] = iso.split('-');
  const monthShort = new Date(`${iso}T12:00:00Z`).toLocaleString('en-US', {
    month: 'short',
    timeZone: 'UTC',
  });
  return `${monthShort} ${parseInt(d, 10)}, ${y}`;
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
}
